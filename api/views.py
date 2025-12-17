import os
import mimetypes
import tempfile
import uuid
from datetime import datetime, timedelta
from rest_framework.decorators import action

from django.http import HttpResponse, Http404
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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def protected_media(request, file_path):
    file_name = file_path.split("/")[-1]

    # --- check permissions ---
    progress_image = ProgressImage.objects.filter(image=file_path, user=request.user).first()
    progress_video = ProgressVideo.objects.filter(video=file_path, user=request.user).first()

    if not progress_image and not progress_video:
        allowed_prefix = os.path.join("progress_videos", str(request.user.id)) + os.sep
        if not file_path.startswith(allowed_prefix):
            raise Http404("You do not have permission to access this file.")

    full_file_path = os.path.join(settings.MEDIA_ROOT, file_path)
    if not os.path.exists(full_file_path):
        raise Http404("File not found.")

    is_private = (progress_image and not progress_image.is_public) or \
                 (progress_video and not progress_video.is_public)

    if is_private:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        decrypt_file(full_file_path, request.user, tmp_path)
        serve_path = tmp_path
    else:
        serve_path = full_file_path

    content_type, _ = mimetypes.guess_type(serve_path)
    if not content_type:
        content_type = "application/octet-stream"

    with open(serve_path, "rb") as f:
        response = HttpResponse(f.read(), content_type=content_type)
        response["Content-Disposition"] = f"inline; filename={file_name}"
        return response


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
                "image": request.build_absolute_uri(reverse("protected_media", args=[img.image.name])),
                "date": img.date.isoformat()
            }
            for img in images
        ]

        return Response({"images": image_data})


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


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]


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


class RegisterView(generics.CreateAPIView):
    queryset = CustomUser.objects.all()
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer


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
            full_path = os.path.join(settings.MEDIA_ROOT, img.image.name)
            if not os.path.exists(full_path):
                return Response({"detail": f"File missing on server: {img.image.name}"}, status=400)

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(full_path)[1])
            decrypt_file(full_path, request.user, tmp.name)
            img_paths.append(tmp.name)
            temp_files.append(tmp.name)

        if fps <= 0:
            return Response({"detail": "fps must be > 0"}, status=400)

        user_folder = os.path.join(settings.MEDIA_ROOT, "progress_videos", str(request.user.id))
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
            final.write_videofile(out_path, fps=max(1, int(round(fps))),
                                  codec="libx264", audio=False, logger=None)
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

        rel_path = os.path.join("progress_videos", str(request.user.id), out_name).replace("\\", "/")
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

        expected_prefix = os.path.join("progress_videos", str(request.user.id)) + os.sep
        if not video_rel_path.startswith(expected_prefix):
            return Response({"detail": "Not allowed to upload this file."}, status=403)

        full_path = os.path.join(settings.MEDIA_ROOT, video_rel_path)
        if not os.path.exists(full_path):
            return Response({"detail": "Video not found on server."}, status=404)

        return Response({
            "status": "accepted",
            "detail": f"Upload to {platform} is not yet configured.",
            "video_rel_path": video_rel_path,
            "caption_echo": caption
        }, status=202)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_progress_image(request, image_id):
    try:
        progress_image = ProgressImage.objects.get(id=image_id)
    except ProgressImage.DoesNotExist:
        return Response({"detail": "Progress image not found."}, status=404)

    if progress_image.user != request.user:
        return Response({"detail": "You do not have permission to delete this image."}, status=403)

    image_path = progress_image.image.path
    if os.path.exists(image_path):
        try:
            os.remove(image_path)
        except Exception as e:
            progress_image.delete()
            return Response({
                "message": "Progress image deleted, but file could not be removed.",
                "error": str(e)
            }, status=200)

    progress_image.delete()
    return Response({"message": "Progress image deleted successfully."}, status=200)


class MaxUnitViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MaxUnit.objects.all()
    serializer_class = MaxUnitSerializer
    permission_classes = [permissions.IsAuthenticated]


class MaxCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MaxCategory.objects.select_related("unit").all()
    serializer_class = MaxCategorySerializer
    permission_classes = [permissions.IsAuthenticated]


# =========================================================
#                  FIXED MaxDataViewSet
# =========================================================

class MaxDataViewSet(viewsets.ModelViewSet):
    queryset = MaxData.objects.all()
    serializer_class = MaxDataSerializer
    permission_classes = [permissions.IsAuthenticated]

    # FIX: do NOT allow text like “category” to be treated as a pk
    lookup_value_regex = '[0-9]+'

    @action(detail=False, methods=['get'], url_path='category/(?P<category_id>[0-9]+)')
    def get_by_category(self, request, category_id=None):
        try:
            obj = MaxData.objects.get(
                user=request.user,
                category_id=category_id
            )
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
        qs = MaxData.objects.filter(
            user=request.user,
            category_id=category_id
        ).order_by("date")

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

