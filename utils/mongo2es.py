#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Copy JSON items from MongoDB to Elasticsearch
#
# Copyright (C) 2016 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#

import argparse
import json
import logging
import os

from pymongo import MongoClient

from dateutil import parser

from grimoire_elk.elk.elastic import ElasticSearch

def get_params():
    parser = argparse.ArgumentParser(usage="usage: mongo2es [options]",
                                     description="Import mongo items in ElasticSearch")
    parser.add_argument("-c", "--collection", required=True, help="MongoDB Collection")
    parser.add_argument("-m", "--mongo-host", default='localhost', help="MongoDB Host")
    parser.add_argument("-p", "--mongo-port", default='27017', type=int, help="MongoDB Port")
    parser.add_argument("-e", "--elastic-url", required=True, help="ElasticSearch URL")
    parser.add_argument("-i", "--index", required=True, help="ElasticSearch index in which to import the mongodb items")
    parser.add_argument('-g', '--debug', dest='debug', action='store_true')
    args = parser.parse_args()

    return args

def fetch_mongodb(collection_str, host=None, port=None):
    if host and port:
        client = MongoClient(host, port)
    elif host:
        client = MongoClient(host)
    else:
        client = MongoClient()

    collection = None
    if "." in collection_str:
        subcollections = collection_str.split(".")
        for col in subcollections:
            client = client[col]
        collection = client
    else:
        collection = client[collection_str]

    for item in collection.find():
        item['datetime'] = item['__datetime'].isoformat()
        item['__datetime'] = item['__datetime'].isoformat()
        item['date'] = item['__date']
        # Field [_id] is a metadata field and cannot be added inside a document.
        item['mongo_id'] = item.pop('_id')  # The _id can not in the data in ES
        item['mongo_type'] = item.pop('_type')  # The _id can not in the data in ES
        yield item

if __name__ == '__main__':

    args = get_params()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')
        logging.debug("Debug mode activated")
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

    logging.info("Importing items from %s to %s/%s", args.collection, args.elastic_url, args.index)

    elastic = ElasticSearch(args.elastic_url, args.index)

    mongo_items = fetch_mongodb(args.collection, args.mongo_host, args.mongo_port)
    elastic.bulk_upload_sync(mongo_items, "mongo_id")
