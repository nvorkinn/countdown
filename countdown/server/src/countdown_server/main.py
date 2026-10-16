import logging

from countdown_core import main
from countdown_server.server_target import ServerTarget

logger = logging.getLogger(__name__)


def run() -> None:
    """The `countdown-server` console script: the app, rendering for a separate screen."""
    main(ServerTarget)


if __name__ == "__main__":
    run()
