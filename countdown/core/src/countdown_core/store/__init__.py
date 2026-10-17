"""The contract of the shared Redis store: its key names (`keys`) and what is stored under them (`models`).

Providers (countdown-providers) write it; renderers will read it. Both import it from here, so the two sides can't
drift apart. The broker (auth-broker) writes the subscription sets, so `keys` must match what it writes too.
"""
