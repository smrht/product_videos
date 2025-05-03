# core/middleware.py

def get_client_ip(request):
    """Get the client's real IP address from the request."""
    # Check for X-Forwarded-For header, common when behind proxies/load balancers
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # The header can contain multiple IPs (client, proxy1, proxy2, ...).
        # The first one is typically the client's real IP.
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        # If X-Forwarded-For is not present, fall back to REMOTE_ADDR
        ip = request.META.get('REMOTE_ADDR')
    return ip

class IPMiddleware:
    """
    Middleware to attach the client's IP address to the request object.
    This makes the IP easily accessible in views or other middleware.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        # One-time configuration and initialization.

    def __call__(self, request):
        # Code to be executed for each request before
        # the view (and later middleware) are called.
        request.client_ip = get_client_ip(request)

        response = self.get_response(request)

        # Code to be executed for each request/response after
        # the view is called.

        return response
