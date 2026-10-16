"""The energy panel's client: electricity readings from Glow, cached so each page only asks for what's new."""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar, override

from pydantic import TypeAdapter

from countdown_core.core.abstract_client import DEFAULT_TIMEOUT, AbstractClient
from countdown_core.glow.energy_panel import EnergyPanel
from countdown_core.glow.models import Entity, Readings
from countdown_core.i18n import N_

if TYPE_CHECKING:
    from pydantic import JsonValue

    from countdown_api.models import Glowmarkt
    from countdown_credentials.registration import Registration

DECEMBER = 12


def _get_utc_offset(now: datetime | None = None) -> str:
    """Glow's offset param: minutes to add to local time to get UTC, so BST is "-60".

    An aware `now` keeps its own zone; a naive or missing one is taken as local time.
    """
    # Evaluated per call, not as a default argument: that would freeze the offset at
    # import time, wrong for a long-running device after the clocks change.
    now = now or datetime.now().astimezone()
    utc_offset = (now if now.tzinfo else now.astimezone()).utcoffset()
    if utc_offset is None:
        msg = "Could not find a UTC offset"
        raise ValueError(msg)
    # total_seconds(), not .seconds: a negative timedelta keeps a positive .seconds.
    return str(round(-utc_offset.total_seconds() / 60))


class GlowClient(AbstractClient):
    """The energy panel's pages in turn, a new one each update: the last 24 hours, 31 days, and this year."""

    # update() runs every minute to rotate the page; each page only asks Glow for fresh
    # readings this often and redraws from cache otherwise.
    refresh_intervals = {0: timedelta(minutes=10), 1: timedelta(minutes=10), 2: timedelta(minutes=10)}
    refresh_intervals: ClassVar[dict[int, timedelta]] = {
        0: timedelta(minutes=30),
        1: timedelta(hours=3),
        2: timedelta(hours=12),
    }
    panel_title = N_("Energy")
    base_url = "https://api.glowmarkt.com/api/v0-1"
    app_id = "b0f1b774-a586-4f72-9edd-27ead8aa7a8d"
    resource_id = None
    token = None

    def __init__(self, registration: Registration, config: Glowmarkt) -> None:
        """`config` is the broker's glowmarkt section: the Glow account to sign in with."""
        super().__init__(registration, config)
        self.page_index = 0
        self.username = config.username
        self.password = config.password
        self.glow_cache = {0: {}, 1: {}, 2: {}}
        self.cache_utc_offset: timedelta | None = None
        self.last_fetched: dict[int, datetime] = {}
        self.open_readings: dict[int, tuple[datetime, float]] = {}

    @override
    def _initialise(self) -> None:
        self._authenticate()
        self.resource_id = self.get_electricity_resource_id()

    def _authenticate(self) -> None:
        """Fetch and store the session token."""
        url = f"{self.base_url}/auth"
        payload = {"username": self.username, "password": self.password, "applicationId": self.app_id}
        response = self.session.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        self.token = response.json().get("token")

    def _request(self, method: str, endpoint: str, params: dict[str, str] | None = None) -> JsonValue:
        """Centralised request wrapper implementing lazy authentication."""
        if not self.token:
            self._authenticate()
        if not self.token:
            return None

        headers = {"token": self.token, "applicationId": self.app_id, "Content-Type": "application/json"}

        url = f"{self.base_url}{endpoint}"
        response = self.session.request(method, url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)

        # Token expired: authenticate again and retry once.
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            self._authenticate()
            if not self.token:
                return None
            headers["token"] = self.token
            response = self.session.request(method, url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)

        response.raise_for_status()
        return response.json()

    def get_electricity_resource_id(self) -> str:
        """Find the id of the meter's electricity consumption, the resource every page reads."""
        json = self._request("GET", "/virtualentity")
        entity_list_adapter = TypeAdapter(list[Entity])
        entities = entity_list_adapter.validate_python(json)
        for resource in entities[0].resources:
            if resource.name == "electricity consumption":
                return resource.resourceId
        msg = "Could not find a resource with name 'electricity consumption'"
        raise ValueError(msg)

    @override
    def _update(self) -> EnergyPanel:
        current = datetime.now().astimezone()
        window_start, period, bucket_delta = self._page_config(self.page_index, current)
        open_start = self._truncate(current, period)

        # Bucket-aligned, or it never matches the cache's keys.
        window_start = self._truncate(window_start, period)

        # Glow aggregates days and months at the offset we send, so buckets cached
        # before the clocks changed are the wrong ones - start afresh.
        if current.utcoffset() != self.cache_utc_offset:
            self.glow_cache = {0: {}, 1: {}, 2: {}}
            self.last_fetched = {}
            self.open_readings = {}
            self.cache_utc_offset = current.utcoffset()

        cache = self.glow_cache[self.page_index]
        last_fetched = self.last_fetched.get(self.page_index)
        if last_fetched is None or current - last_fetched >= self.refresh_intervals[self.page_index]:
            self._refresh(cache, window_start, open_start, current, period=period, bucket_delta=bucket_delta)
            self.last_fetched[self.page_index] = current

        # Drop what's aged out of the window (page 2's starts on 1st Jan, so never).
        for stale in [b for b in cache if b < window_start]:
            del cache[stale]

        usage = [
            {"start": bucket_start.isoformat(), "kwh": round(value, 3)} for bucket_start, value in sorted(cache.items())
        ]
        # Between refreshes this is the reading from the last fetch - possibly for
        # a bucket that has since closed, but still the latest figure we have.
        open_reading = self.open_readings.get(self.page_index)
        if open_reading is not None and open_reading[0] >= window_start and open_reading[0] not in cache:
            open_bucket_start, open_value = open_reading
            usage.append({"start": open_bucket_start.isoformat(), "kwh": round(open_value, 3)})

        self.glow_cache[self.page_index] = cache
        panel = EnergyPanel(usage, self.page_index)
        self.page_index = (self.page_index + 1) % 3
        return panel

    def _refresh(  # noqa: PLR0913 -- the window and its buckets, as _update() worked them out
        self,
        cache: dict[datetime, float],
        window_start: datetime,
        open_start: datetime,
        current: datetime,
        *,
        period: str,
        bucket_delta: timedelta,
    ) -> None:
        # The earliest closed bucket missing from the cache, if any.
        missing_from = None
        cursor = window_start
        while cursor < open_start:
            if cursor not in cache:
                missing_from = cursor if missing_from is None else missing_from
            cursor = self._next_bucket_start(cursor, period, bucket_delta)

        if missing_from is not None:
            fetched = self._get_readings(missing_from, current, period)
        else:
            fetched = self._get_readings(open_start, current, period)

        self.open_readings.pop(self.page_index, None)
        for bucket_start, value in fetched:
            if bucket_start < open_start:
                cache[bucket_start] = value  # closed - safe to keep forever
            else:
                self.open_readings[self.page_index] = (bucket_start, value)  # open - never cached

    def _page_config(self, page_index: int, current: datetime) -> tuple[datetime, str, timedelta]:
        match page_index:
            case 0:
                window_start = current - timedelta(hours=24)
                return window_start, "PT1H", timedelta(hours=1)
            case 1:
                window_start = current - timedelta(days=31)
                return window_start, "P1D", timedelta(days=1)
            case 2:
                window_start = current.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                return window_start, "P1M", timedelta()  # months handled specially below
        msg = f"page_index must be 0, 1 or 2 - got {page_index}"
        raise ValueError(msg)

    def _truncate(self, dt: datetime, period: str) -> datetime:
        """Round a datetime down to the start of its bucket."""
        if period == "PT1H":
            return dt.replace(minute=0, second=0, microsecond=0)
        if period == "P1D":
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        # Otherwise it's a month.
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _next_bucket_start(self, dt: datetime, period: str, bucket_delta: timedelta) -> datetime:
        """Step forward one bucket; months aren't a fixed timedelta."""
        if period == "P1M":
            if dt.month == DECEMBER:
                return dt.replace(year=dt.year + 1, month=1)
            return dt.replace(month=dt.month + 1)
        return dt + bucket_delta

    def _get_readings(self, dt_from: datetime, dt_to: datetime, period: str) -> list[Readings]:
        """(bucket start, kWh) pairs, summed by Glow per period: "PT1H", "P1D" or "P1M"."""
        response = self.session.get(
            f"{self.base_url}/resource/{self.resource_id}/readings",
            headers={"applicationId": self.app_id, "token": self.token},
            params={
                "from": dt_from.strftime("%Y-%m-%dT%H:%M:%S"),
                "to": dt_to.strftime("%Y-%m-%dT%H:%M:%S"),
                "period": period,
                "offset": _get_utc_offset(dt_from),
                "function": "sum",
            },
        )
        response.raise_for_status()

        # With an offset set, Glow labels each bucket with its *local* wall-clock
        # start encoded as if it were UTC (06:00 BST comes back as 06:00Z). Read the
        # wall clock as UTC, then relabel it with the request's zone so the keys
        # are the same instants as the bucket starts _update() walks.
        local_tz = dt_from.tzinfo
        readings = []
        for ts, value in response.json()["data"]:
            # Glow has sent both epoch seconds and ISO strings.
            if isinstance(ts, (int, float)):
                wall_clock = datetime.fromtimestamp(ts, tz=UTC)
            else:
                wall_clock = datetime.fromisoformat(ts)
            readings.append((wall_clock.replace(tzinfo=local_tz), value))

        return readings
