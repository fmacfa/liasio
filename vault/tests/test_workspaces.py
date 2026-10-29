import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from vault.access import collections_for
from vault.models import (
    CollectionAccess,
    GroupAccess,
    SharingPreset,
    TeamGroup,
    Workspace,
    WorkspaceMember,
)
from vault.workspaces import attach_collection, transfer_collection
from .test_batch_api import api_key
from .test_security import IsolatedFiles


class WorkspaceTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account()
        self.member = self.account("member@example.com")
        self.outsider = self.account("outsider@example.com", admin=True)
        self.folder = self.collection(self.owner)
        self.workspace = Workspace.objects.create(owner=self.owner, name="Studio")
        WorkspaceMember.objects.create(workspace=self.workspace, user=self.owner)
        WorkspaceMember.objects.create(workspace=self.workspace, user=self.member)
        self.group = TeamGroup.objects.create(workspace=self.workspace, name="Clients")
        self.group.members.add(self.member)
        attach_collection(self.owner, self.folder.pk, self.workspace.pk)
        self.client.force_login(self.owner)

    def test_group_grants_are_explicit_and_shared_across_browser_and_api(self):
        self.assertFalse(collections_for(self.member).exists())
        self.client.post(
            f"/collections/{self.folder.pk}/workspace/",
            {"action": "groups", str(self.group.pk): "reader"},
        )
        self.assertTrue(collections_for(self.member).filter(pk=self.folder.pk).exists())
        self.assertFalse(collections_for(self.member, write=True).exists())
        self.client.force_login(self.member)
        self.assertContains(
            self.client.get(f"/collections/{self.folder.pk}/"), self.folder.title
        )
        _key, token = api_key(self.member)
        auth = {"HTTP_AUTHORIZATION": "Bearer " + token}
        self.assertEqual(
            len(self.client.get("/api/v1/collections/", **auth).json()["results"]), 1
        )
        response = self.client.post(
            f"/api/v1/collections/{self.folder.pk}/links/",
            json.dumps({"label": "Client"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="workspace-001",
            **auth,
        )
        self.assertEqual(response.status_code, 404)
        GroupAccess.objects.filter(group=self.group).update(role="collaborator")
        response = self.client.post(
            f"/api/v1/collections/{self.folder.pk}/links/",
            json.dumps({"label": "Client"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="workspace-001",
            **auth,
        )
        self.assertEqual(response.status_code, 201)
        self.client.force_login(self.owner)
        self.client.post(
            f"/workspaces/{self.workspace.pk}/",
            {"action": "remove", "member": self.member.workspace_memberships.get().pk},
        )
        self.assertFalse(collections_for(self.member).exists())
        self.assertFalse(self.group.members.filter(pk=self.member.pk).exists())

    def test_workspace_administration_and_cross_workspace_grants_are_bounded(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.post(
                f"/workspaces/{self.workspace.pk}/",
                {"action": "settings", "name": "Bad"},
            ).status_code,
            404,
        )
        self.client.force_login(self.outsider)
        self.assertEqual(
            self.client.get(f"/workspaces/{self.workspace.pk}/").status_code, 404
        )
        foreign = Workspace.objects.create(owner=self.outsider, name="Foreign")
        other_group = TeamGroup.objects.create(workspace=foreign, name="Wrong group")
        other_group.members.add(self.member)
        GroupAccess.objects.create(
            group=other_group, collection=self.folder, role="collaborator"
        )
        self.assertFalse(collections_for(self.member).exists())

    def test_transfer_preserves_bytes_links_and_moves_quota(self):
        from vault.models import ShareLink

        link = ShareLink.objects.create(collection=self.folder, label="Existing")
        before = link.public_url
        blobs = list(self.folder.files.values_list("blob", flat=True))
        moved = transfer_collection(self.owner, self.folder.pk, self.member)
        self.owner.refresh_from_db()
        self.member.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual((self.owner.used_bytes, self.member.used_bytes), (0, 5))
        self.assertEqual(link.public_url, before)
        self.assertEqual(list(moved.files.values_list("blob", flat=True)), blobs)
        self.assertFalse(collections_for(self.owner).exists())
        self.assertTrue(collections_for(self.member, write=True).exists())
        self.assertEqual(
            self.client.post(
                f"/collections/{self.folder.pk}/workspace/",
                {
                    "action": "transfer",
                    "email": self.outsider.email,
                    "confirm": "transfer",
                },
            ).status_code,
            404,
        )

    def test_transfer_refuses_wrong_workspace_and_full_quota(self):
        with self.assertRaises(ValidationError):
            transfer_collection(self.owner, self.folder.pk, self.outsider)
        self.member.quota_bytes = 0
        self.member.save()
        with self.assertRaises(ValidationError):
            transfer_collection(self.owner, self.folder.pk, self.member)
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.owner_id, self.owner.pk)

    def test_leaving_workspace_removes_group_but_keeps_individual_grants(self):
        GroupAccess.objects.create(
            collection=self.folder, group=self.group, role="collaborator"
        )
        CollectionAccess.objects.create(
            collection=self.folder, user=self.member, role="reader"
        )
        attach_collection(self.owner, self.folder.pk, None)
        self.assertFalse(GroupAccess.objects.filter(collection=self.folder).exists())
        self.assertTrue(collections_for(self.member).exists())
        self.assertFalse(collections_for(self.member, write=True).exists())

    def test_workspace_forms_create_and_update_groups(self):
        created = self.client.post("/workspaces/", {"name": "New studio"})
        self.assertEqual(created.status_code, 302)
        created = self.client.post(
            f"/workspaces/{self.workspace.pk}/groups/new/",
            {"name": "Team", "members": [self.member.pk]},
        )
        self.assertEqual(created.status_code, 302)
        self.assertTrue(
            TeamGroup.objects.get(name="Team")
            .members.filter(pk=self.member.pk)
            .exists()
        )
        response = self.client.post(
            f"/workspaces/{self.workspace.pk}/groups/new/",
            {"name": "Wrong", "members": [self.outsider.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamGroup.objects.filter(name="Wrong").exists())

    def test_duplicate_group_name_is_validation_error_and_delete_removes_access(self):
        response = self.client.post(
            f"/workspaces/{self.workspace.pk}/groups/new/", {"name": self.group.name}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("name"))
        self.assertEqual(self.workspace.groups.count(), 1)
        GroupAccess.objects.create(
            collection=self.folder, group=self.group, role="reader"
        )
        url = f"/workspaces/{self.workspace.pk}/groups/{self.group.pk}/"
        self.assertTrue(collections_for(self.member).exists())
        self.assertEqual(self.client.post(url, {"action": "delete"}).status_code, 404)
        self.assertEqual(
            self.client.post(
                url, {"action": "delete", "confirm": "delete"}
            ).status_code,
            302,
        )
        self.assertFalse(collections_for(self.member).exists())
        self.assertFalse(GroupAccess.objects.exists())

    def test_workspace_mfa_cannot_be_enabled_without_owner_enrollment(self):
        response = self.client.post(
            f"/workspaces/{self.workspace.pk}/",
            {"action": "settings", "name": "Studio", "require_mfa": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.workspace.refresh_from_db()
        self.assertFalse(self.workspace.require_mfa)


class PresetTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account()
        self.folder = self.collection(self.owner)
        self.preset = SharingPreset.objects.create(
            owner=self.owner, name="Clients", expires_in_days=30, max_downloads=3
        )
        _key, raw = api_key(self.owner)
        self.auth = {"HTTP_AUTHORIZATION": "Bearer " + raw}

    def create(self, data, key="preset-request-001"):
        return self.client.post(
            f"/api/v1/collections/{self.folder.pk}/link-batches/",
            json.dumps(data),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
            **self.auth,
        )

    def test_batch_policy_override_and_replay_survives_preset_deletion(self):
        data = {
            "name": "Clients",
            "names": ["A", "B"],
            "conditions": {"preset_id": str(self.preset.pk), "max_downloads": 2},
        }
        response = self.create(data)
        self.assertEqual(response.status_code, 201, response.content)
        link = self.folder.links.first()
        self.assertEqual(link.max_downloads, 2)
        self.assertLess(
            abs(
                (link.expires_at - timezone.now()).total_seconds()
                - timedelta(days=30).total_seconds()
            ),
            5,
        )
        self.preset.delete()
        self.assertEqual(self.create(data).json(), response.json())

    def test_foreign_preset_denied_and_password_not_saved_in_preset(self):
        self.preset.owner = self.account("other@example.com")
        self.preset.save()
        data = {
            "name": "Denied",
            "names": ["A"],
            "conditions": {"preset_id": str(self.preset.pk)},
        }
        self.assertEqual(self.create(data).status_code, 404)
        self.preset.owner = self.owner
        self.preset.password_required = True
        self.preset.save()
        self.assertEqual(self.create(data).status_code, 400)
        data["conditions"]["password"] = "x"
        self.assertEqual(self.create(data).status_code, 201)
