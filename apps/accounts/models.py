from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Profile(models.Model):
    class Role(models.TextChoices):
        ADMIN_PUSAT = "ADMIN_PUSAT", "Admin Pusat"
        SATKER = "SATKER", "Satker"
        VIEWER = "VIEWER", "Viewer"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    satker_code = models.CharField(max_length=32, blank=True)
    satker_name = models.CharField(max_length=255, blank=True)
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["role"]),
            models.Index(fields=["satker_code"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"

    @property
    def is_admin_pusat(self):
        return self.role == self.Role.ADMIN_PUSAT

    @property
    def is_satker(self):
        return self.role == self.Role.SATKER

    @property
    def is_viewer(self):
        return self.role == self.Role.VIEWER


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_profile(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if created:
        Profile.objects.create(user=instance)
