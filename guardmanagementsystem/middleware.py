from django.http import HttpResponseForbidden
from django.urls import Resolver404, resolve

from .role_access import user_can_access


class RoleAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            url_name = resolve(request.path_info).url_name
        except Resolver404:
            url_name = None

        if url_name and not user_can_access(request.user, url_name):
            return HttpResponseForbidden("You do not have permission to access this page.")

        return self.get_response(request)
