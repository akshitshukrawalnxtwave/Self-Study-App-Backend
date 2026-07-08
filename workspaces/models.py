import uuid

from django.db import models


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(null=True, blank=True)
    title = models.CharField(max_length=255)
    topic_slug = models.SlugField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_panel_html_url = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def to_dict(self):
        return {
            "id": str(self.id),
            "title": self.title,
            "topic_slug": self.topic_slug,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="sessions"
    )
    last_active_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    sdk_session_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-last_active_at"]


class Message(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=20)
    message_type = models.CharField(max_length=20, default="text")
    content = models.TextField()
    turn_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def to_dict(self):
        return {
            "id": str(self.id),
            "role": self.role,
            "type": self.message_type,
            "content": self.content,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }
