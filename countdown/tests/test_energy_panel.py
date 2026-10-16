import countdown_core  # noqa: F401, I001 -- see test_display_snapshots.py for why this comes first
import pytest
from PIL import ImageDraw

from countdown_core.glow.energy_panel import EnergyPanel
import itertools


@pytest.mark.parametrize(
    ("peak", "step", "axis_max"),
    [
        (0.07, 0.02, 0.08),
        (0.4, 0.1, 0.4),
        (1.65, 0.5, 2.0),
        (9.9, 2.5, 10.0),
        (15, 5, 15),
        (178.6, 50, 200),
        (310, 100, 400),
        (0, 1.0, 1.0),
    ],
)
def test_y_scale_picks_a_round_step_and_the_first_tick_above_the_peak(peak, step, axis_max):
    assert EnergyPanel._y_scale(peak) == pytest.approx((step, axis_max))


def test_render_draws_y_axis_tick_labels_left_of_the_bars():
    readings = [{"start": f"2026-09-25T{hour:02d}:00:00", "kwh": 1.5} for hour in range(24)]

    img = EnergyPanel(readings, 0).render(260, 200)

    # The chart is pushed right to leave a column for the tick labels, and that
    # column has them in it.
    bar_row = img.crop((0, 150, 260, 151))
    assert bar_row.getbbox()[0] > 15
    assert img.crop((0, 40, 15, 150)).getbbox() is not None


@pytest.mark.parametrize("clock_format", ["12h", "24h"])
@pytest.mark.parametrize("width", [249, 518, 790])
def test_hourly_chart_labels_fit_and_never_overlap_or_drop_the_period(clock_format, width, monkeypatch):
    readings = [{"start": f"2026-09-25T{hour:02d}:00:00+05:30", "kwh": 1.5} for hour in range(24)]
    timestamps = [row["start"] for row in readings]
    labels = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, font=None, anchor=None, **kwargs):
        if anchor == "ma":
            labels.append((text, draw.textbbox(xy, text, font=font, anchor=anchor), font.size))
        return original(draw, xy, text, *args, font=font, anchor=anchor, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    EnergyPanel(readings, 0).render(width, 200, clock_format=clock_format)

    assert len(labels) >= 2
    for text, (left, top, right, bottom), size in labels:
        assert 0 <= left < right <= width
        assert 0 <= top < bottom <= 196
        assert size == 12
        assert text.endswith((" AM", " PM")) if clock_format == "12h" else len(text) == 5
    assert all(first[1][2] + 8 <= second[1][0] for first, second in itertools.pairwise(labels))
    assert [row["start"] for row in readings] == timestamps


@pytest.mark.parametrize(
    ("page", "readings"),
    [
        (1, [{"start": f"2026-09-{day:02d}T00:00:00+01:00", "kwh": day} for day in range(1, 25)]),
        (2, [{"start": f"2026-{month:02d}-01T00:00:00+01:00", "kwh": month} for month in range(1, 13)]),
    ],
)
def test_non_clock_energy_pages_do_not_change(page, readings):
    panel = EnergyPanel(readings, page)

    assert panel.render(249, 200).tobytes() == panel.render(249, 200, clock_format="12h").tobytes()


def test_hourly_labels_handle_midnight_and_noon_in_the_data_time_zone(monkeypatch):
    texts = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    readings = [{"start": f"2026-09-25T{hour:02d}:07:00-04:00", "kwh": 1.5} for hour in (0, 12)]
    panel = EnergyPanel(readings, 0)
    panel.render(518, 200, clock_format="12h")
    assert {"12:07 AM", "12:07 PM"} <= set(texts)
    texts.clear()
    panel.render(518, 200)
    assert {"00:07", "12:07"} <= set(texts)
