from django.urls import path

from workspaces import views

urlpatterns = [
    path("workspaces/", views.workspaces_collection, name="workspaces-collection"),
    path(
        "workspaces/<uuid:workspace_id>/lessons/",
        views.list_lessons,
        name="workspace-lessons",
    ),
    path(
        "workspaces/<uuid:workspace_id>/messages/",
        views.list_messages,
        name="workspace-messages",
    ),
    path(
        "workspaces/<uuid:workspace_id>/chat/",
        views.chat,
        name="workspace-chat",
    ),
]
