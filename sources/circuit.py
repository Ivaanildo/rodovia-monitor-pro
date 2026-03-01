"""Circuit breakers para APIs externas."""
import pybreaker

here_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="HERE_API",
)

google_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="Google_API",
)

tomtom_incidents_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="TomTom_Incidents_API",
)

tomtom_flow_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="TomTom_Flow_API",
)

# Alias para retrocompatibilidade
tomtom_breaker = tomtom_incidents_breaker
