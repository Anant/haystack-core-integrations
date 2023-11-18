import json
import logging
from typing import Any, Dict, List, Optional, Union

import requests
from pydantic.dataclasses import dataclass

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@dataclass
class Response:
    id: str
    score: float
    metadata: dict
    values: list


@dataclass
class QueryResponse:
    matches: List[Response]

    def get(self, key):
        return self.__dict__[key]


class AstraClient:
    """
    A client for interacting with an Astra index via JSON API
    """

    def __init__(
        self,
        astra_id: str,
        astra_region:str,
        astra_application_token: str,
        keyspace_name: str,
        collection_name: str,
        embedding_dim: int,
        similarity_function: str,
    ):
        self.astra_id = astra_id
        self.astra_application_token = astra_application_token
        self.astra_region = astra_region
        self.keyspace_name = keyspace_name
        self.collection_name = collection_name
        self.embedding_dim = embedding_dim
        self.similarity_function = similarity_function

        self.request_url = f"https://{self.astra_id}-{self.astra_region}.apps.astra.datastax.com/api/json/v1/{self.keyspace_name}/{self.collection_name}"
        self.request_header = {
            "x-cassandra-token": self.astra_application_token,
            "Content-Type": "application/json",
        }
        self.create_url = f"https://{self.astra_id}-{self.astra_region}.apps.astra.datastax.com/api/json/v1/{self.keyspace_name}"

        index_exists = self.find_index()
        if not index_exists:
            self.create_index()

    def find_index(self):
        find_query =  {
            "findCollections": {
                "options": {
                    "explain" : True
                }
            }
        }
        response = requests.request("POST", self.create_url, headers=self.request_header, data=json.dumps(find_query))
        response_dict = json.loads(response.text)

        if response.status_code == 200:
            if "status" in response_dict:
                collection_name_matches = list(
                    filter(
                        lambda d: d['name'] == self.collection_name,
                        response_dict["status"]["collections"]
                    )
                )

                if len(collection_name_matches)==0:
                    logger.warning(f"Astra collection {self.collection_name} not found under {self.keyspace_name}. Will be created.")
                    return False

                collection_embedding_dim = collection_name_matches[0]["options"]["vector"]["dimension"]
                if collection_embedding_dim != self.embedding_dim:
                    raise Exception(f"Collection vector dimension is not valid, expected {self.embedding_dim}, found {collection_embedding_dim}")

            else:
                raise Exception(f"status not in response: {response.text}")

        else:
            raise Exception(f"Astra DB not available. Status code: {response.status_code}, {response.text}")
            # Retry or handle error better

        return True

    def create_index(self):
        create_query = { "createCollection": {
            "name": self.collection_name,
            "options": {
              "vector": {
                  "dimension": self.embedding_dim,
                  "metric": self.similarity_function
              }
            }
          }
        }
        response = requests.request("POST", self.create_url, headers=self.request_header, data=json.dumps(create_query))
        response_dict = json.loads(response.text)
        if response.status_code == 200 and "status" in response_dict:
            logger.info(f"Collection {self.collection_name} created: {response.text}")
        else:
            raise Exception(f"Create Astra collection ailed with the following error: status code {response.status_code}, {response.text}")


    def query(
        self,
        vector: Optional[List[float]] = None,
        filter: Optional[Dict[str, Union[str, float, int, bool, List, dict]]] = None,
        top_k: Optional[int] = None,
        namespace: Optional[str] = None,
        include_metadata: Optional[bool] = None,
        include_values: Optional[bool] = None,
    ) -> QueryResponse:
        """
        The Query operation searches a namespace, using a query vector.
        It retrieves the ids of the most similar items in a namespace, along with their similarity scores.

        Args:
            vector (List[float]): The query vector. This should be the same length as the dimension of the index
                                  being queried. Each `query()` request can contain only one of the parameters
                                  `queries`, `id` or `vector`.. [optional]
            top_k (int): The number of results to return for each query. Must be an integer greater than 1.
            filter (Dict[str, Union[str, float, int, bool, List, dict]):
                    The filter to apply. You can use vector metadata to limit your search. [optional]
            include_metadata (bool): Indicates whether metadata is included in the response as well as the ids.
                                     If omitted the server will use the default value of False  [optional]
            include_values (bool): Indicates whether values/vector is included in the response as well as the ids.
                                     If omitted the server will use the default value of False  [optional]

        Returns: object which contains the list of the closest vectors as ScoredVector objects,
                 and namespace name.
        """
        # get vector data and scores
        responses = self._query(vector, top_k, filter)
        # include_metadata means return all columns in the table (including text that got embedded)
        # include_values means return the vector of the embedding for the searched items
        formatted_response = self._format_query_response(
            responses, include_metadata, include_values
        )

        return formatted_response

    @staticmethod
    def _format_query_response(responses, include_metadata, include_values):
        final_res = []
        for response in responses:
            id = response.pop("_id")
            score = response.pop("$similarity")
            _values = response.pop("$vector")
            values = _values if include_values else []
            metadata = response if include_metadata else dict()
            rsp = Response(id, score, metadata, values)
            final_res.append(rsp)
        return QueryResponse(final_res)

    def _query(self, vector, top_k, filters=None):
        score_query = {
            "find": {
                "sort": {"$vector": vector},
                "projection": {"$similarity": 1},
                "options": {"limit": top_k},
            }
        }
        query = {"find": {"sort": {"$vector": vector}, "options": {"limit": top_k}}}
        print(
            requests.request(
                "POST",
                self.request_url,
                headers=self.request_header,
                data=json.dumps(score_query),
            ).json()
        )
        if filters is not None:
            score_query["find"]["filter"] = filters
            query["find"]["filter"] = filters
        similarity_score = requests.request(
            "POST",
            self.request_url,
            headers=self.request_header,
            data=json.dumps(score_query),
        ).json()["data"]["documents"]
        result = requests.request(
            "POST",
            self.request_url,
            headers=self.request_header,
            data=json.dumps(query),
        ).json()["data"]["documents"]
        response = []
        for elt1 in similarity_score:
            for elt2 in result:
                if elt1["_id"] == elt2["_id"]:
                    response.append(elt1 | elt2)
        return response


    def upsert(self, to_upsert):
        to_insert = []
        upserted_ids = []
        not_upserted_ids = []
        for record in to_upsert:
            record_id = record[0]
            record_text = record[2]["text"]
            record_embedding = record[1]

            reserved_keys = ["id", "_id", "chunk"]
            record_metadata = {}
            for k in list(record[2].keys()):
                if k not in reserved_keys:
                    record_metadata[k] = record[2][k]

            # check if id exists:
            query = json.dumps({
                "findOne": {
                    "filter": {
                        "_id": record_id
                }}})
            result = requests.request(
                "POST",
                self.request_url,
                headers=self.request_header,
                data=query,
            )

            # if the id doesn't exist, prepare record for inserting
            if json.loads(result.text)['data']['document'] == None:
                to_insert.append({"_id": record_id, "$vector": record_embedding, "metadata": record_metadata})

            # else, update record with that id
            else:
                query = json.dumps({
                    "findOneAndUpdate": {
                        "filter": {
                            "_id": record_id
                        },
                        "update": {
                            "$set": {
                                "$vector": record_embedding,
                                "metadata": record_metadata,
                            }},
                        "options": {
                            "returnDocument": "after"
                        }
                      }
                    })
                result = requests.request(
                    "POST",
                    self.request_url,
                    headers=self.request_header,
                    data=query,
                )

                if json.loads(result.text)["status"]["matchedCount"] == 1 and json.loads(result.text)["status"]["modifiedCount"] == 1:
                    upserted_ids.append(record_id)

        # now insert the records stored in to_insert
        if len(to_insert) > 0:
            query = json.dumps({
            "insertMany": {
                "documents": to_insert }})
            result = requests.request(
                    "POST",
                    self.request_url,
                    headers=self.request_header,
                    data=query,
                )
            for inserted_id in json.loads(result.text)["status"]["insertedIds"]:
                upserted_ids.append(inserted_id)

        return list(set(upserted_ids))


    def delete(
        self,
        ids: Optional[List[str]] = None,
        delete_all: Optional[bool] = None,
        filter: Optional[Dict[str, Union[str, float, int, bool, List, dict]]] = None,
    ) -> Dict[str, Any]:
        if delete_all:
            query = {"deleteMany": {}}
        if ids is not None:
            query = {"deleteMany": {"filter": {"_id": {"$in": ids}}}}
        if filter is not None:
            query = {"deleteMany": {"filter": filter}}
        response = requests.request(
            "POST",
            self.request_url,
            headers=self.request_header,
            data=json.dumps(query),
        )
        print(response.text)
        return response

    @staticmethod
    def describe_index_stats():
        return {
            "dimension": 1536,
            "index_fullness": 0.0,
            "namespaces": {},
            "total_vector_count": 0,
            "total_document_count": 0
        }
