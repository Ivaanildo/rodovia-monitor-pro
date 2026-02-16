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
