import logging
logger = logging.getLogger(__name__)
from core.crawler import Crawler
import json

def is_valid(json_object):
    return 'id' in json_object and 'sections' in json_object

class JACrawler(Crawler):

    def crawl(self) -> None:
        json_file = '/home/vectara/data/file.json'
        with open(json_file, 'r') as file:
            data = file.read()
        json_array = json.loads(data)
        if not isinstance(json_array, list):
            raise Exception("JSON file must contain an array of JSON objects")

        logger.info(f"indexing {len(json_array)} JSON documents from JSON file")
        count = 0
        for json_object in json_array:
            if count % 100 == 0:
                logger.info(f"finished {count} documents so far")
            if is_valid(json_object):
                self.indexer.index_document(json_object)
                count += 1
            else:
                logger.warning(f"invalid JSON object: {json_object}")
