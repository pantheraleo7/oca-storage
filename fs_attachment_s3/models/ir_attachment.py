# Copyright 2025 ACSONE SA/NV
# Copyright 2025 XCG SAS
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from odoo import models

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _storage_write_option(self, fs):
        option = super()._storage_write_option(fs)
        mimetype = self.env.context.get("mimetype")
        if mimetype:
            root_fs = self.env["fs.storage"]._get_root_filesystem(fs)
            if hasattr(root_fs, "s3"):
                option["ContentType"] = mimetype
        return option

    def _get_x_sendfile_path(self):
        self.ensure_one()
        storage = self.fs_storage_id
        if storage.is_s3_storage:
            return self._get_s3_x_sendfile_path()
        return super()._get_x_sendfile_path()

    def _fs_use_x_sendfile(self):
        self.ensure_one()
        storage = self.fs_storage_id
        if storage.is_s3_storage:
            return storage.use_x_sendfile_to_serve_internal_url
        return super()._fs_use_x_sendfile()

    def _get_s3_x_sendfile_path(self):
        """Generate the X-Accel-Redirect path for S3 storage.

        This method is used to generate the path for S3 storage when using
        X-Accel-Redirect. It constructs the path based on the S3 bucket and
        file path, ensuring that it is compatible with the S3 storage
        configuration and the Odoo file storage system.

        Args:
            attachment (IrAttachment): The attachment record for which the
                X-Accel-Redirect path is being generated.
        Returns:
            str: The X-Accel-Redirect path for the S3 storage.

        The path is formatted as:
            /fs_x_sendfile/<scheme>/<netloc>/<path>

        where:
        - `<scheme>` is the scheme of the base URL (e.g., 'https').
        - `<netloc>` is the netloc of the base URL (e.g., 's3.amazonaws.com').
        - `<path>` is the path to the file in the S3 bucket, including the
          bucket name
        """
        fs, storage_code, filename = self._get_fs_parts()
        storage = self.env["fs.storage"].sudo().get_by_code(storage_code)
        root_fs = storage._get_root_filesystem(fs)
        directory_path = storage.get_directory_path().strip("/").rstrip("/")

        s3_client = root_fs.s3
        endpoint_url_virtual_host_style = s3_client.meta.endpoint_url.rstrip('/')
        parsed_endpoint_url_vhs = urlparse(endpoint_url_virtual_host_style)
        netloc_parts = parsed_endpoint_url_vhs.netloc.split('.')

        endpoint_url_path_style = f'{parsed_endpoint_url_vhs.scheme}://{'.'.join(netloc_parts[1:])}'
        bucket_name = netloc_parts[0]
        region = netloc_parts[2]

        if storage.s3_uses_signed_url_for_x_sendfile:
            temp_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url_path_style,
                region_name=region,
                aws_access_key_id=root_fs.key,
                aws_secret_access_key=root_fs.secret,
                config=Config(s3={"addressing_style": "virtual"}, signature_version="s3v4"),
            )
            file_url = temp_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name, "Key": f'{directory_path}/{filename}'},
                ExpiresIn=storage.s3_signed_url_expiration,
            )
        else:
            file_url = (
                f"{endpoint_url_virtual_host_style}/{directory_path}/{filename.lstrip('/')}"
            )

        parsed_url = urlparse(file_url)
        path = parsed_url.path.strip("/")
        query = parsed_url.query
        redirect_path = f"/fs_x_sendfile/{parsed_url.scheme}/{parsed_url.netloc}/{path}"
        if query:
            redirect_path += f"?{query}"
        return redirect_path
