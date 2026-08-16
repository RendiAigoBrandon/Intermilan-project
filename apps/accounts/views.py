import logging

from django.contrib.auth import authenticate, login
from django.contrib.auth.views import LoginView
from django.http import HttpRequest

logger = logging.getLogger("apps.accounts")


class DebugLoginView(LoginView):
    """
    Custom LoginView with debug logging for troubleshooting login issues.

    Logs (without showing passwords):
    - username_input
    - user_found
    - authenticate_result
    - backend used
    """

    def form_valid(self, form):
        username_input = form.cleaned_data.get("username", "")

        logger.warning("[LOGIN DEBUG] ====== LOGIN ATTEMPT ======")
        logger.warning(f"[LOGIN DEBUG] username_input={username_input}")

        # Get user from database first
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user_found = User.objects.filter(username=username_input).exists()
        logger.warning(f"[LOGIN DEBUG] user_found={user_found}")

        if user_found:
            user_obj = User.objects.get(username=username_input)
            logger.warning(f"[LOGIN DEBUG] user.is_active={user_obj.is_active}")
            logger.warning(f"[LOGIN DEBUG] user.is_superuser={user_obj.is_superuser}")

        # Authenticate
        authenticate_kwargs = {
            "username": username_input,
            "password": form.cleaned_data.get("password", ""),
        }
        user = authenticate(self.request, **authenticate_kwargs)

        if user is not None:
            logger.warning(f"[LOGIN DEBUG] authenticate_result=True")
            logger.warning(f"[LOGIN DEBUG] user={user.username}")
            logger.warning(f"[LOGIN DEBUG] backend={getattr(user, 'backend', 'N/A')}")
            logger.warning("[LOGIN DEBUG] ====== LOGIN SUCCESS ======")

            # Check if user is active
            if not user.is_active:
                logger.error("[LOGIN DEBUG] User is inactive!")
                form.add_error(None, "Akun ini tidak aktif. Hubungi administrator.")
                return self.form_invalid(form)

            login(self.request, user, backend=user.backend if hasattr(user, 'backend') else None)
            return super().form_valid(form)
        else:
            logger.warning("[LOGIN DEBUG] authenticate_result=False")
            logger.warning("[LOGIN DEBUG] ====== LOGIN FAILED ======")

        return super().form_invalid(form)
