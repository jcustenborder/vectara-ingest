import logging
logger = logging.getLogger(__name__)
import markdown
from core.crawler import Crawler
from core.utils import html_to_text
import json

import synapseclient

class SynapseCrawler(Crawler):

    def _index_wiki_content(self, syn: str, wiki_id: str, description: str, url: str, source: str, wiki_type: str = "study") -> None:
        try:
            wiki_dict = syn.getWiki(wiki_id)
        except Exception as e:
            logger.info(f"Error getting wiki {wiki_id}: {e}")
            return

        study_text = html_to_text(markdown.markdown(wiki_dict['markdown']))
        doc = {
            "id": wiki_id,
            "metadata": {
                'url': url,
                'source': source,
                'created': wiki_dict['createdOn']
            },
            "sections": [
                { "text": f"{wiki_type} description: {description}\n" },
                { "text": study_text },
            ]
        }
        title = wiki_dict['title']
        if title and len(title)>0:
            doc['title'] = title
        else:
            doc['title'] = f'{wiki_type} {wiki_id}'
        succeeded = self.indexer.index_document(doc)
        if succeeded:
            logger.info(f"Indexed {wiki_type} {wiki_id}")
        else:
            logger.info(f"Error indexing {wiki_type} {wiki_id}")


    def crawl(self) -> None:
        # setup synapse client
        syn = synapseclient.Synapse()
        syn.login(authToken=self.cfg.synapse_crawler.synapse_token)

        source = self.cfg.synapse_crawler.get("source", "tables")

        # Crawl and index all programs
        programs_id = self.cfg.synapse_crawler.programs_id
        df = syn.tableQuery(f"SELECT * from {programs_id};", resultsAs="rowset").asDataFrame()
        df = df[['Program', 'Long Description']]
        df.columns = ['program', 'description']
        for tup in df.itertuples(index=False):
            logger.info(f"Indexing program {tup.program}")
            url = f'https://adknowledgeportal.synapse.org/Explore/Programs/DetailsPage?Program={tup.program}'
            doc = {
                "id": tup.program,
                "title": f'Program {tup.program}',
                "metadata": {
                    'url': url,
                    'source': source,
                },
                "sections": [{ "text": tup.description }],
            }
            succeeded = self.indexer.index_document(doc)
            if succeeded:
                logger.info(f"Indexed study {doc['id']}")
            else:
                logger.info(f"Error indexing study {doc['id']}")
        logger.info(f"Finished indexing all programs (total={len(df)})")

        # crawl and index all studies
        studies_id = self.cfg.synapse_crawler.studies_id
        df = syn.tableQuery(f"SELECT * from {studies_id};", resultsAs="rowset").asDataFrame()
        df = df[['Program', 'Study', 'Study_Description', 'Methods']]
        df.columns = ['program', 'study', 'description', 'methods']
        for tup in df.itertuples(index=False):
            logger.info(f"Indexing study {tup.study}")
            url = f'https://adknowledgeportal.synapse.org/Explore/Studies/DetailsPage/StudyDetails?Study={tup.study}'
            self._index_wiki_content(syn, tup.study, tup.description, url, source, wiki_type="study")

            if tup.methods is None:
                continue
            methods = [m.strip() for m in tup.methods.split(',')]
            logger.info(f"For study {tup.study}, we have {len(methods)} methods to index")
            url = f'https://adknowledgeportal.synapse.org/Explore/Studies/DetailsPage/StudyDetails?Study={tup.study}#Methods'
            for method in methods:
                logger.info(f"Indexing method {method}")
                self._index_wiki_content(syn, method, f"Study {tup.study}, Method {method}", url, source, wiki_type="method")

        logger.info(f"Finished indexing all studies (total={len(df)})")
