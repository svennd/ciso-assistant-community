"""Task completers can execute tasks and submit evidence within their perimeter."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.translation import override
from rest_framework.test import APIClient

from core.models import TaskNode, TaskTemplate
from core.utils import RoleCodename, get_translated_builtin_role_name
from iam.models import Folder, Role, RoleAssignment, User


@pytest.fixture
def task_completer(db):
    folder = Folder.objects.create(
        name="Task domain",
        parent_folder=Folder.get_root_folder(),
        content_type=Folder.ContentType.DOMAIN,
    )
    user = User.objects.create_user(email="task-completer@example.com")
    role = Role.objects.get(name=RoleCodename.TASK_COMPLETER.value, builtin=True)
    assignment = RoleAssignment.objects.create(user=user, role=role, folder=folder)
    assignment.perimeter_folders.add(folder)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, folder, role


def test_task_completer_can_complete_task_and_submit_evidence(
    task_completer, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    client, folder, _ = task_completer
    response = client.post(
        "/api/task-templates/",
        {
            "name": "Review backups",
            "folder": str(folder.id),
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    template_id = response.data["id"]
    response = client.patch(
        f"/api/task-templates/{template_id}/",
        {
            "description": "Verify restore results",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    template = TaskTemplate.objects.get(pk=template_id)
    node = TaskNode.objects.filter(task_template=template).first()
    if node is None:
        node = TaskNode.objects.create(task_template=template, folder=folder)
    response = client.get(f"/api/task-nodes/{node.id}/")
    assert response.status_code == 200, response.data
    response = client.patch(
        f"/api/task-nodes/{node.id}/",
        {
            "status": "completed",
            "observation": "Restore verified",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    node.refresh_from_db()
    assert node.status == "completed"
    response = client.post(
        "/api/evidences/",
        {
            "name": "Restore report",
            "folder": str(folder.id),
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    evidence_id = response.data["id"]
    response = client.post(
        "/api/evidence-revisions/",
        {
            "evidence": evidence_id,
            "folder": str(folder.id),
            "task_node": str(node.id),
            "observation": "Restore succeeded",
            "attachment": SimpleUploadedFile(
                "restore.txt", b"Restore verification passed", content_type="text/plain"
            ),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.data
    assert node.evidence_revisions.filter(evidence_id=evidence_id).exists()
    assert client.get(f"/api/evidences/{evidence_id}/").status_code == 200
    assert client.delete(f"/api/task-nodes/{node.id}/").status_code == 403
    assert client.delete(f"/api/evidences/{evidence_id}/").status_code == 403


def test_task_completer_cannot_write_outside_perimeter(task_completer):
    client, _, role = task_completer
    other = Folder.objects.create(
        name="Other domain",
        parent_folder=Folder.get_root_folder(),
        content_type=Folder.ContentType.DOMAIN,
    )
    template = TaskTemplate.objects.create(name="Private task", folder=other)
    node = TaskNode.objects.create(task_template=template, folder=other)
    assert client.get(f"/api/task-nodes/{node.id}/").status_code in (403, 404)
    assert client.patch(
        f"/api/task-nodes/{node.id}/",
        {
            "status": "completed",
        },
        format="json",
    ).status_code in (403, 404)
    assert (
        client.post(
            "/api/evidences/",
            {
                "name": "Forbidden",
                "folder": str(other.id),
            },
            format="json",
        ).status_code
        == 403
    )
    permissions = set(role.permissions.values_list("codename", flat=True))
    assert not any(p.startswith("delete_") for p in permissions)
    assert all(
        p.split("_", 1)[1]
        in {
            "folder",
            "tasktemplate",
            "tasknode",
            "evidence",
            "evidencerevision",
        }
        for p in permissions
    )


def test_task_completer_display_name():
    with override("en"):
        assert get_translated_builtin_role_name("BI-RL-TCO") == "Task completer"
    with override("de"):
        assert get_translated_builtin_role_name("BI-RL-TCO") == "Task completer"
