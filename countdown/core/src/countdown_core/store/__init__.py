"""The contract of the shared Redis store: its key names (`keys`) and what is stored under them (`models`).

Providers (countdown-providers) write it; the render pool reads it. Both import it from here, so the two sides can't
drift apart. The broker writes the subscriptions, configs and render queue, so `keys` must match what it
writes too.
"""
