from django.urls import path, re_path

from workspaces import views

urlpatterns = [
    re_path(
        r"^workspaces/(?P<workspace_id>[0-9a-f-]+)/(?P<file_path>.+)$",
        views.serve_workspace_file,
        name="workspace-file",
    ),
]
