import logging
from importlib.resources import contents
import time

from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.files.file import File
from furl import furl
import os
import tempfile
from core.crawler import Crawler

class SharepointCrawler(Crawler):
    """
    A crawler implementation for ingesting and indexing documents from SharePoint sites.

    The SharepointCrawler class connects to SharePoint, authenticates using specified credentials,
    recursively crawls specified folders, downloads supported document types, and indexes them
    using the configured indexing service.
    """

    def new_url(self, /, *paths) -> furl:
        """
        Constructs a new URL by copying the base URL and appending additional path segments.

        Args:
            *paths (str): One or more path segments to append.

        Returns:
            furl.furl: A new furl object representing the resulting URL.
        """
        result = self.base_url.copy()
        for p in paths:
            result.path = os.path.join(str(result.path), str(p))
        return result

    def download_url(self, file):
        """
        Generates a direct download URL for the given SharePoint file.

        Args:
            file: A SharePoint file object with a server-relative URL attribute.

        Returns:
            str: Direct URL to download the specified file.
        """
        download_url = self.new_url("_layouts/15/download.aspx")
        download_url.args['SourceUrl'] = file.serverRelativeUrl
        return download_url.url

    def configure_sharepoint_context(self) -> None:
        """
        Configures and authenticates the SharePoint client context based on provided crawler configuration.

        Supported authentication methods:
            - User credentials (username/password)
            - Client credentials (client_id/client_secret)
            - Client certificates (client_id, thumbprint, certificate file, and optional passphrase)

        Raises:
            Exception: If an unsupported authentication type is specified in configuration.
        """
        self.base_url = furl(self.cfg.sharepoint_crawler.team_site_url)
        self.team_site_url = self.cfg.sharepoint_crawler.team_site_url
        logging.info(f"team_site_url = '{self.team_site_url}'")
        auth_type = self.cfg.sharepoint_crawler.get('auth_type', 'user_credentials')
        context = ClientContext(self.team_site_url)

        match auth_type:
            case 'user_credentials':
                allow_ntlm = bool(self.cfg.sharepoint_crawler.get('allow_ntlm', 'True'))
                self.sharepoint_context = context.with_user_credentials(
                    self.cfg.sharepoint_crawler.username,
                    self.cfg.sharepoint_crawler.password,
                    allow_ntlm
                )
            case 'client_credentials':
                self.sharepoint_context = context.with_client_credentials(
                    self.cfg.sharepoint_crawler.client_id,
                    self.cfg.sharepoint_crawler.client_secret
                )
            case 'client_certificate':
                cert_settings = {
                    'client_id': self.cfg.sharepoint_crawler.client_id,
                    'thumbprint': self.cfg.sharepoint_crawler.cert_thumbprint,
                    'cert_path': self.cfg.sharepoint_crawler.cert_path
                }
                if self.cfg.sharepoint_crawler.get('cert_passphrase'):
                    cert_settings['passphrase'] = self.cfg.sharepoint_crawler.cert_passphrase
                self.sharepoint_context = context.with_client_certificate(
                    self.cfg.sharepoint_crawler.tenant_id, **cert_settings
                )
            case _:
                raise Exception(f"Unknown auth_type '{auth_type}'")

    def execute_with_retry(self, func):
        retries = self.cfg.sharepoint_crawler.get("retry_attempts", 3)
        delay = self.cfg.sharepoint_crawler.get("retry_delay", 5)
        for attempt in range(retries):
            try:
                return func()
            except Exception as e:
                if attempt == retries - 1:
                    raise
                logging.warning(f"Attempt {attempt + 1} failed with error: {e}. Retrying in {delay} seconds...")
                time.sleep(delay)

    def crawl_folder(self) -> None:
        """
        Crawls a specified SharePoint folder to locate, download, and index files.

        The method retrieves files from SharePoint and filters them based on supported extensions.
        Supported file types include: .pdf, .md, .odt, .doc, .docx, .ppt, .pptx, .txt, .html, .htm, .lxml, .rtf, and .epub.

        Files matching the supported types are downloaded temporarily, indexed, and then deleted.

        Logging:
            - Warns about skipped unsupported file types.
            - Logs indexing errors.
        """
        recursive = self.cfg.sharepoint_crawler.get('recursive', False)
        target_folder = self.cfg.sharepoint_crawler.target_folder
        logging.info(f"target_folder = '{target_folder}' recursive = {recursive}")

        root_folder = self.sharepoint_context.web.get_folder_by_server_relative_path(target_folder)
        files = root_folder.get_files(recursive=recursive).execute_query()

        supported_extensions = {
            ".pdf", ".md", ".odt", ".doc", ".docx", ".ppt",
            ".pptx", ".txt", ".html", ".htm", ".lxml",
            ".rtf", ".epub"
        }

        for file in files:
            filename, file_extension = os.path.splitext(file.name)
            if file_extension.lower() not in supported_extensions:
                logging.warning(f"Skipping {file.serverRelativeUrl} due to unsupported file type '{file_extension}'.")
                continue

            metadata = {'url': self.download_url(file)}

            with tempfile.NamedTemporaryFile(suffix=file_extension, mode="wb", delete=False) as f:
                logging.debug(f"Downloading and writing content for {file.unique_id} to {f.name}")
                file.download_session(f).execute_query()
                f.flush()
                f.close()

                try:
                    succeeded = self.indexer.index_file(f.name, metadata['url'], metadata, file.unique_id)
                finally:
                    if os.path.exists(f.name):
                        os.remove(f.name)

                if not succeeded:
                    logging.error(f"Error indexing {file.unique_id} - {file.serverRelativeUrl}")

    def crawl(self) -> None:
        """
        Initiates the crawling process based on the crawler configuration.

        Steps performed:
            1. Configures SharePoint client context.
            2. Executes crawling operation based on the configured mode:
                - 'folder': Initiates crawling of a SharePoint folder.
                - 'list': Initiates crawling of a SharePoint list and its attachments.

        Raises:
            Exception: If the specified crawl mode is unknown or unsupported.
        """
        self.configure_sharepoint_context()
        mode = self.cfg.sharepoint_crawler.mode
        logging.info(f"mode = '{mode}'")

        match mode:
            case 'folder':
                self.crawl_folder()
            case 'list':
                self.crawl_list()
            case _:
                raise Exception(f"Unknown mode '{mode}'")


    def crawl_list(self) -> None:
        list_name = self.cfg.sharepoint_crawler.target_list
        target_list = self.sharepoint_context.web.lists.get_by_title(list_name)
        self.sharepoint_context.load(target_list, ['Id'])
        items = target_list.items.get().execute_query()

        allowed_fields = self.cfg.sharepoint_crawler.get("list_item_metadata_fields", [])
        for item in items:
            metadata = {k: v for k, v in item.properties.items() if k in allowed_fields}
            item_id = item.properties["ID"]
            metadata["list_id"] = str(target_list.id)
            metadata["list_item_id"] = str(item_id)
            attachment_files = target_list.get_item_by_id(item_id).attachment_files.get().execute_query()
            for attachment in attachment_files:
                filename = os.path.basename(attachment.server_relative_url)
                extension = os.path.splitext(filename)[1]
                doc_id = f"{target_list.id}-{item_id}-{filename}"
                attachment_url = f"{self.team_site_url}{attachment.server_relative_url}"
                metadata["url"] = attachment_url

                with tempfile.NamedTemporaryFile(suffix=extension, mode="wb", delete=False) as f:
                    self.sharepoint_context.web.get_file_by_server_relative_url(attachment.server_relative_url).download(f).execute_query()
                    try:
                        succeeded = self.indexer.index_file(f.name, attachment_url, metadata, doc_id)
                    finally:
                        if os.path.exists(f.name):
                            os.remove(f.name)

                    if not succeeded:
                        logging.error(f"Error indexing attachment {filename} for list item {item_id}")
