from django.views.i18n import set_language, JavaScriptCatalog
from django.urls import path
from vault import (
    views,
    mfa,
    trash,
    resumable,
    operations,
    previews,
    api,
    integrations,
    help,
    brand_media,
    batches,
    api_batches,
    team_access,
)

urlpatterns = [
    path(
        "collections/<uuid:pk>/access/",
        team_access.collection_access,
        name="collection_access",
    ),
    path(
        "collections/<uuid:pk>/download/",
        views.collection_download,
        name="collection_download",
    ),
    path(
        "team/members/<int:pk>/access/", team_access.member_access, name="member_access"
    ),
    path("help/", help.index, name="help"),
    path("help/<slug:slug>/", help.guide, name="guide"),
    path("language/", set_language, name="set_language"),
    path("jsi18n/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
    path("automations/", integrations.index, name="integrations"),
    path(
        "automations/keys/<uuid:pk>/revoke/",
        integrations.revoke_token,
        name="api_key_revoke",
    ),
    path(
        "automations/webhooks/<uuid:pk>/", integrations.webhook_detail, name="webhook"
    ),
    path("api/v1/collections/", api.collections, name="api_collections"),
    path(
        "api/v1/collections/create/",
        api.create_collection,
        name="api_collection_create",
    ),
    path("api/v1/collections/<uuid:pk>/", api.collection),
    path("api/v1/collections/<uuid:pk>/links/", api.create_link),
    path(
        "api/v1/collections/<uuid:pk>/link-batches/",
        api_batches.create,
        name="api_link_batch_create",
    ),
    path("api/v1/link-batches/", api_batches.index, name="api_link_batches"),
    path("api/v1/link-batches/<uuid:pk>/", api_batches.detail, name="api_link_batch"),
    path(
        "api/v1/link-batches/<uuid:pk>/export/<str:format>/",
        api_batches.export,
        name="api_link_batch_export",
    ),
    path("api/v1/collections/<uuid:pk>/uploads/start/", api.upload_start),
    path("api/v1/collections/<uuid:pk>/uploads/pending/", api.uploads_pending),
    path("api/v1/uploads/<uuid:pk>/status/", api.upload_status),
    path("api/v1/uploads/<uuid:pk>/parts/<uuid:part_id>/chunk/", api.upload_chunk),
    path("api/v1/uploads/<uuid:pk>/finish/", api.upload_finish),
    path("api/v1/uploads/<uuid:pk>/cancel/", api.upload_cancel),
    path("api/v1/links/", api.links),
    path("api/v1/links/<uuid:pk>/", api.link),
    path("api/v1/links/<uuid:pk>/revoke/", api.revoke_link),
    path("", views.dashboard, name="dashboard"),
    path("setup/", views.setup, name="setup"),
    path("login/", views.sign_in, name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("forgot-password/", views.password_reset, name="password_reset"),
    path(
        "reset/<uidb64>/<token>/",
        views.ResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("invite/<str:token>/", views.accept_invitation, name="accept_invitation"),
    path("login/mfa/", mfa.challenge, name="mfa_challenge"),
    path("account/mfa/", mfa.settings_view, name="mfa_settings"),
    path("account/mfa/qr/", mfa.qr, name="mfa_qr"),
    path("account/", views.account, name="account"),
    path("collections/", views.collections, name="collections"),
    path("upload/", views.upload_page, name="upload"),
    path("collections/<uuid:pk>/uploads/start/", resumable.start, name="upload_start"),
    path(
        "collections/<uuid:pk>/uploads/pending/",
        resumable.pending,
        name="upload_pending",
    ),
    path("uploads/<uuid:pk>/status/", resumable.status, name="upload_status"),
    path(
        "uploads/<uuid:pk>/parts/<uuid:part_id>/chunk/",
        resumable.chunk,
        name="upload_chunk",
    ),
    path("uploads/<uuid:pk>/finish/", resumable.finish, name="upload_finish"),
    path("uploads/<uuid:pk>/cancel/", resumable.cancel, name="upload_cancel"),
    path("collections/new/", views.collection_create, name="collection_create"),
    path("collections/<uuid:pk>/", views.collection_detail, name="collection"),
    path("collections/<uuid:pk>/edit/", views.collection_edit, name="collection_edit"),
    path(
        "collections/<uuid:pk>/upload/",
        views.collection_upload,
        name="collection_upload",
    ),
    path(
        "collections/<uuid:pk>/delete/",
        views.collection_delete,
        name="collection_delete",
    ),
    path("collections/<uuid:pk>/link/", views.link_create, name="link_create"),
    path(
        "collections/<uuid:pk>/links/batch/", batches.prepare, name="link_batch_prepare"
    ),
    path("link-batches/", batches.index, name="link_batches"),
    path(
        "link-batches/template/<str:format>/",
        batches.template,
        name="link_batch_template",
    ),
    path("link-batches/<uuid:pk>/", batches.detail, name="link_batch"),
    path(
        "link-batches/<uuid:pk>/export/<str:format>/",
        batches.export,
        name="link_batch_export",
    ),
    path("link-batches/<uuid:pk>/delete/", batches.delete, name="link_batch_delete"),
    path("links/", views.links, name="links"),
    path("links/<uuid:pk>/rename/", batches.rename, name="link_rename"),
    path("links/<uuid:pk>/revoke/", views.link_revoke, name="link_revoke"),
    path("links/<uuid:pk>/previews/", previews.toggle, name="link_previews"),
    path("activity/", views.activity, name="activity"),
    path("branding/", views.branding, name="branding"),
    path("branding/media/<str:kind>/", brand_media.own, name="brand_media"),
    path(
        "s/<str:token>/brand/<str:kind>/", brand_media.shared, name="share_brand_media"
    ),
    path("trash/", trash.index, name="trash"),
    path("trash/<str:kind>/<uuid:pk>/", trash.action, name="trash_action"),
    path("storage/", views.storage, name="storage"),
    path("storage/delete/", views.storage_delete, name="storage_delete"),
    path("team/", views.team, name="team"),
    path(
        "team/invitations/<uuid:pk>/revoke/",
        views.invitation_revoke,
        name="invitation_revoke",
    ),
    path("team/members/<int:pk>/toggle/", views.member_toggle, name="member_toggle"),
    path("s/<str:token>/", views.share, name="share"),
    path("s/<str:token>/unlock/", views.unlock, name="unlock"),
    path("s/<str:token>/download/", views.download, name="download"),
    path("s/<str:token>/preview/<uuid:asset_id>/", previews.image, name="file_preview"),
    path("operations/", operations.index, name="operations"),
    path("operations/members/<int:pk>/quota/", operations.quota, name="member_quota"),
    path("ready/", operations.readiness, name="readiness"),
    path("health/", views.health, name="health"),
    path(
        "robots.txt",
        lambda request: views.HttpResponse(
            "User-agent: *\nDisallow: /\n", content_type="text/plain"
        ),
    ),
]
handler404 = "vault.views.error404"
handler500 = "vault.views.error500"
