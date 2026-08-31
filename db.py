from pymongo import MongoClient

import config

_client = None


def get_mongo_client():
    global _client
    if _client is None:
        _client = MongoClient(config.MONGO_URI)
    return _client


def get_videos_collection():
    client = get_mongo_client()
    return client[config.MONGO_DB_NAME][config.MONGO_COLLECTION]


def get_folders_collection():
    client = get_mongo_client()
    return client[config.MONGO_DB_NAME][config.FOLDERS_COLLECTION]
