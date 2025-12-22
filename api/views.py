import os
import mimetypes
import tempfile
import uuid
from datetime import datetime, timedelta
from rest_framework.decorators import action

from django.http import Http404, FileResponse
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework import generics, permissions, viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from moviepy import ImageClip, concatenate_videoclips

from progress_tracking.models import ProgressImage, Category, ProgressVideo
from .serializers import *
from user.models import CustomUser
from .base import CsrfExemptAPIView
from .utils.encryption import encrypt_file, decrypt_file
from django.urls import reverse

from core.models import FeedbackMessage

import boto3
from .utils.wasabi import generate_signed_url, get_decrypted_temp_file


# =========================
# Wasabi S3 Client
# =========================
s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_S3_REGION_NAME,
    endpoint_url=settings.AWS_S3_ENDPOINT_URL,
)


# =========================
# Feedback
# =========================
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_feedback(request):
    body = request.data.get("body", "").strip()
    if not body:
        return Response({"error": "Feedback cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)

    feedback = FeedbackMessage.objects.create(user=request.user, body=body)

    return Response({
        "message": "Feedback submitted successfully",
        "id": feedback.id,
        "body": feedback.body,
    }, status=status.HTTP_201_CREATED)


# =========================
# Protected media (Wasabi signed URL)
# =========================


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def protected_media(request, file_path):
    """
    Returns signed URL if public, otherwise downloads, decrypts and serves file.
    """
    progress_image = ProgressImage.objects.filter(image=file_path, user=request.user).first()
    progress_video = ProgressVideo.objects.filter(video=file_path, user=request.user).first()

    # Access control
    if not progress_image and not progress_video:
        allowed_prefix = os.path.join("progress_videos", str(request.user.id)) + os.sep
        if not file_path.startswith(allowed_prefix):
            raise Http404("You do not have permission to access this file.")

    is_private = (
        (progress_image and not progress_image.is_public) or
        (progress_video and not progress_video.is_public)
    )

    if not is_private:
        # Public → return signed URL
        url = generate_signed_url(file_path, expires=3600)
        return Response({"url": url})

    # Private → download & decrypt temp file
    temp_path = get_decrypted_temp_file(file_path, request.user)
    file_name = os.path.basename(file_path)
    content_type, _ = mimetypes.guess_type(file_name)
    if not content_type:
        content_type = "application/octet-stream"

    response = FileResponse(
        open(temp_path, "rb"),
        content_type=content_type,
        as_attachment=False,
    )
    response["Content-Disposition"] = f'inline; filename="{file_name}"'

    # Delete temp file after response is closed
    old_close = response.close
    def close():
        old_close()
        if os.path.exists(temp_path):
            os.remove(temp_path)
    response.close = close

    return response

# =========================
# User category progress
# =========================
class UserCategoryProgressView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        username = self.kwargs['username']
        category_name = self.kwargs['category_name']

        if request.user.username != username:
            raise PermissionDenied("You are not allowed to view another user's progress.")

        category = get_object_or_404(Category, name__iexact=category_name)

        images = ProgressImage.objects.filter(
            user=request.user,
            category=category
        ).order_by('date')

        image_data = [
            {
                "id": img.id,
                "image": request.build_absolute_uri(
                    reverse("protected_media", args=[img.image.name])
                ),
                "date": img.date.isoformat()
            }
            for img in images
        ]

        return Response({"images": image_data})


# =========================
# Logout
# =========================
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({"detail": "Successfully logged out."}, status=status.HTTP_205_RESET_CONTENT)
        except KeyError:
            return Response({"detail": "Refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)
        except TokenError:
            return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)


# =========================
# Categories
# =========================
class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]


# =========================
# Progress Images
# =========================
class ProgressImageViewSet(viewsets.ModelViewSet):
    serializer_class = ProgressImageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ProgressImage.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        image = self.request.FILES.get('image')
        if not image:
            raise ValidationError({"image": "No image file provided."})

        if not image.content_type.startswith('image/'):
            raise ValidationError({"image": "Only image files are allowed."})

        obj = serializer.save(user=self.request.user, is_public=False)
        encrypt_file(obj.image.path, self.request.user)


class ProgressImageCreateView(generics.CreateAPIView):
    serializer_class = ProgressImageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ProgressImage.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        category_name = self.request.data.get("category")
        category = get_object_or_404(Category, name=category_name)

        obj = serializer.save(user=self.request.user, category=category, is_public=False)
        encrypt_file(obj.image.path, self.request.user)


# =========================
# Register
# =========================
class RegisterView(generics.CreateAPIView):
    queryset = CustomUser.objects.all()
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer


# =========================
# Create Progress Video
# =========================
class CreateProgressVideoView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not request.user.is_premium:
            week_ago = now() - timedelta(days=7)
            videos_last_week = ProgressVideo.objects.filter(
                user=request.user,
                created_at__gte=week_ago
            ).count()
            if videos_last_week >= 10:
                return Response(
                    {"detail": "Free users can only create up to 10 videos per week."},
                    status=403
                )

        category_name = request.data.get("category")
        start_index = int(request.data.get("start_index", 0))
        end_index = int(request.data.get("end_index", -1))
        fps = float(request.data.get("fps", 2.0))
        width = request.data.get("width")
        height = request.data.get("height")
        order = (request.data.get("order") or "oldest").lower()

        if not category_name:
            return Response({"detail": "category is required"}, status=400)

        category = get_object_or_404(Category, name__iexact=category_name)

        qs = ProgressImage.objects.filter(user=request.user, category=category)
        qs = qs.order_by("-date" if order == "newest" else "date")
        images = list(qs)
        if not images:
            return Response({"detail": "No images found in this category."}, status=400)

        n = len(images)
        if end_index < 0 or end_index >= n:
            end_index = n - 1
        start_index = max(0, min(start_index, n - 1))
        end_index = max(0, min(end_index, n - 1))
        if start_index > end_index:
            return Response({"detail": "start_index must be <= end_index"}, status=400)

        img_paths = []
        temp_files = []
        start_date = images[start_index].date
        end_date = images[end_index].date

        for img in images[start_index:end_index + 1]:
            # Generate temp file to decrypt locally
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(img.image.name)[1])
            decrypt_file(img.image.path, request.user, tmp.name)
            img_paths.append(tmp.name)
            temp_files.append(tmp.name)

        if fps <= 0:
            return Response({"detail": "fps must be > 0"}, status=400)

        user_folder = os.path.join("progress_videos", str(request.user.id))
        os.makedirs(user_folder, exist_ok=True)

        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        out_name = f"{stamp}_{uuid.uuid4().hex[:8]}.mp4"
        out_path = os.path.join(user_folder, out_name)

        duration = 1.0 / fps
        clips = []
        try:
            for p in img_paths:
                clip = ImageClip(p).with_duration(duration)
                if width and height:
                    clip = clip.resize(newsize=(int(width), int(height)))
                clips.append(clip)

            final = concatenate_videoclips(clips, method="compose")
            final.write_videofile(
                out_path,
                fps=max(1, int(round(fps))),
                codec="libx264",
                audio=False,
                logger=None
            )
            final.close()
        finally:
            for c in clips:
                try:
                    c.close()
                except Exception:
                    pass
            for tmp_path in temp_files:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        rel_path = os.path.join(user_folder, out_name).replace("\\", "/")

        progress_video = ProgressVideo.objects.create(
            user=request.user,
            category=category,
            video=rel_path,
            is_public=False,
            fps=fps,
            start_date=start_date,
            end_date=end_date
        )

        encrypt_file(out_path, request.user)

        video_url = request.build_absolute_uri(reverse("protected_media", args=[rel_path]))

        total_frames = len(img_paths)
        total_seconds = total_frames * duration

        return Response({
            "message": "Video created successfully!",
            "video_url": video_url,
            "count": total_frames,
            "duration_s": round(total_seconds, 2),
            "fps": fps,
            "start_date": start_date.strftime('%Y-%m-%d %H:%M:%S'),
            "end_date": end_date.strftime('%Y-%m-%d %H:%M:%S'),
        })


# =========================
# Upload video placeholder
# =========================
class UploadVideoView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        platform = (request.data.get("platform") or "").lower()
        video_rel_path = request.data.get("video_rel_path")
        caption = request.data.get("caption", "")

        if platform not in ("instagram", "tiktok"):
            return Response({"detail": "platform must be 'instagram' or 'tiktok'."}, status=400)
        if not video_rel_path:
            return Response({"detail": "video_rel_path is required."}, status=400)

        return Response({
            "status": "accepted",
            "detail": f"Upload to {platform} is not yet configured.",
            "video_rel_path": video_rel_path,
            "caption_echo": caption
        }, status=202)


# =========================
# Delete progress image
# =========================
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_progress_image(request, image_id):
    try:
        progress_image = ProgressImage.objects.get(id=image_id)
    except ProgressImage.DoesNotExist:
        return Response({"detail": "Progress image not found."}, status=404)

    if progress_image.user != request.user:
        return Response({"detail": "You do not have permission to delete this image."}, status=403)

    try:
        s3.delete_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=progress_image.image.name)
    except Exception:
        pass

    progress_image.delete()
    return Response({"message": "Progress image deleted successfully."}, status=200)


# =========================
# Max unit/category/data
# =========================
class MaxUnitViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MaxUnit.objects.all()
    serializer_class = MaxUnitSerializer
    permission_classes = [permissions.IsAuthenticated]


class MaxCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MaxCategory.objects.select_related("unit").all()
    serializer_class = MaxCategorySerializer
    permission_classes = [permissions.IsAuthenticated]


class MaxDataViewSet(viewsets.ModelViewSet):
    queryset = MaxData.objects.all()
    serializer_class = MaxDataSerializer
    permission_classes = [permissions.IsAuthenticated]

    lookup_value_regex = '[0-9]+'

    @action(detail=False, methods=['get'], url_path='category/(?P<category_id>[0-9]+)')
    def get_by_category(self, request, category_id=None):
        try:
            obj = MaxData.objects.get(user=request.user, category_id=category_id)
            serializer = self.get_serializer(obj)
            return Response(serializer.data)
        except MaxData.DoesNotExist:
            return Response({"value": None}, status=200)

    @action(detail=False, methods=['post'], url_path='category/(?P<category_id>[0-9]+)')
    def set_by_category(self, request, category_id=None):
        value = request.data.get("value")
        if value is None:
            return Response({"error": "Value required"}, status=400)

        obj, created = MaxData.objects.update_or_create(
            user=request.user,
            category_id=category_id,
            defaults={"value": value},
        )

        serializer = self.get_serializer(obj)
        return Response(serializer.data, status=201)

    @action(detail=False, methods=['get'], url_path='history/(?P<category_id>[0-9]+)')
    def history(self, request, category_id=None):
        qs = MaxData.objects.filter(user=request.user, category_id=category_id).order_by("date")
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)
