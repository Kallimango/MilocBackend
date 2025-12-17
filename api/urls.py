from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import (
    CreateProgressVideoView,
    UploadVideoView,
    ProgressImageCreateView,
    CategoryViewSet,
    UserCategoryProgressView,
    LogoutView,
    RegisterView,
    protected_media,
    ProgressImageViewSet,
    MaxUnitViewSet,
    MaxCategoryViewSet,
    MaxDataViewSet,
)
from . import views

router = DefaultRouter()
router.register(r"categories", CategoryViewSet, basename="categories")
router.register(r"progress/images", ProgressImageViewSet, basename="progress-images")
router.register(r"max/units", MaxUnitViewSet, basename="max-units")
router.register(r"max/categories", MaxCategoryViewSet, basename="max-categories")
router.register(r"max-data", MaxDataViewSet, basename="max-data")   # ✅ FIXED


urlpatterns = [
    path("progress/delete/<int:image_id>/", views.delete_progress_image, name="delete-progress-image"),

    path("feedback/create/", views.create_feedback, name="create_feedback"),

    path("progress/video/upload/", UploadVideoView.as_view(), name="progress-video-upload"),
    path("progress/video/create/", CreateProgressVideoView.as_view(), name="progress-video-create"),

    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth_logout"),

    path(
        "progress/<str:username>/<str:category_name>/",
        UserCategoryProgressView.as_view(),
        name="user-category-progress"
    ),

    path("progress/create/", ProgressImageCreateView.as_view(), name="create-progress-image"),

    path("media/protected/<path:file_path>", protected_media, name="protected_media"),

    path("", include(router.urls)),
]
