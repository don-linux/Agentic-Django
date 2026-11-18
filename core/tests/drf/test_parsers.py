from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadedfile import UploadedFile
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.api.parsers import JSONParser
from core.tests.payloads import NESTED_TOO_DEEPLY

if TYPE_CHECKING:
    from rest_framework.request import Request


class _JsonView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        return Response({"keys": sorted(request.data)})


class _UploadView(_JsonView):
    parser_classes = (MultiPartParser,)

    def post(self, request: Request) -> Response:
        upload = request.FILES["file"]
        assert isinstance(upload, UploadedFile)
        return Response({"name": upload.name, "size": upload.size})


def _upload(view: type[APIView]) -> Response:
    request = APIRequestFactory().post(
        "/upload/",
        {"file": SimpleUploadedFile("notes.txt", b"hello")},
        format="multipart",
    )
    return view.as_view()(request)


def test_views_reject_multipart_by_default() -> None:
    response = _upload(_JsonView)

    assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert response.data["code"] == "unsupported_media_type"


def test_upload_views_opt_in_to_multipart() -> None:
    response = _upload(_UploadView)

    assert response.status_code == HTTPStatus.OK
    assert response.data == {"name": "notes.txt", "size": 5}


def test_json_nested_too_deeply_is_a_parse_error() -> None:
    request = APIRequestFactory().post(
        "/json/", NESTED_TOO_DEEPLY, content_type="application/json"
    )

    response = _JsonView.as_view()(request)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.data["code"] == "parse_error"


def test_the_default_json_parser_is_the_template_one() -> None:
    assert [JSONParser] == api_settings.DEFAULT_PARSER_CLASSES
