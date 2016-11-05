#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Alerts prototype script
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

import requests

from dateutil import parser

ES_URL = 'http://localhost:9200'
ES_INDEX='maniphestMng'
START = parser.parse('1900-01-01')
END = parser.parse('2100-01-01')

class Alert():

    def __init__(self, es_url=ES_URL, es_index=ES_INDEX, start=START, end=END):
        self.start = start
        self.end = end
        self.es_url = es_url
        self.es_index = es_index

    def get_metrics_data(self):
        """ Get the metrics data from ES """
        # curl -XPOST "http://localhost:9200/maniphestMng/_search" -d'
        url = self.es_url+'/'+self.es_index+'/_search'
        r = requests.post(url, self.get_metrics_query())
        r.raise_for_status()
        return r.json()

    def get_metrics(self):
        """ Get the metrics """
        raise NotImplementedError

    def check_metrics(self, query):
        """ Check that metrics are in the ranges """
        raise NotImplementedError

    def get_metrics_query(self, query):
        """ Return the query to get the metrics """
        raise NotImplementedError


class AlertFromBuckets(Alert):

    def get_metrics(self):
        """ Get the metrics """
        data = self.get_metrics_data()

        buckets = data['aggregations'][ElasticQuery.AGGREGATION_ID]['buckets']

        # Check all items are in the aggregations
        if data['aggregations'][ElasticQuery.AGGREGATION_ID]['sum_other_doc_count'] > 0:
            raise RuntimeError("Not all items in aggregations")

        return buckets

    def check_metrics_ranges(self, ranges, percent=True):
        """
        Check that metrics in buckets are in the ranges defined in the dict.

        Sample dict with ranges:

        ranges = {
            'Unbreak Now!': 10,
            'High': 25
        }

        Unbreak Now! and High are the name of the aggregations.

        If percent, the ranges are in 0-100 range.
        """
        ranges = {
            'Unbreak Now!': 10,
            'High': 25
        }
        total = 0
        metrics = self.get_metrics()
        for agg in metrics :
            total += agg['doc_count']

        # Check ranges
        for agg in metrics:
            for r in ranges:
                if agg['key'] == r:
                    val = agg['doc_count']/total*100
                    if val >= ranges[r]:
                        print("ALERT %s: %i > %i for %s (%s/%s, %s->%s)" %
                              (self.__class__.__name__, val, ranges[r], r,
                               self.es_url, self.es_index, self.start, self.end))

class ElasticQuery():
    """ Helper class for building Elastic queries """

    AGGREGATION_ID = "1"
    AGG_SIZE = 100

    @classmethod
    def get_query_range(cls, start=None, end=None):
        start_iso = None
        end_iso = None

        if start:
            start_iso = '"gte": "%s"' % start.isoformat()
        if end:
            end_iso = '"lte": "%s"' % end.isoformat()

        query_range = """
        {
          "range": {
            "metadata__updated_on": {
              %s,
              %s
            }
          }
        }
        """ % (start_iso, end_iso)

        return query_range

    @classmethod
    def get_query_all(cls, start=None, end=None):
        query_range = cls.get_query_range(start, end)

        query_all = """
          "query": {
            "bool": {
              "must": [
                {
                  "query_string": {
                    "analyze_wildcard": true,
                    "query": "*"
                  }
                },
                %s
              ]
            }
          }
        """ % (query_range)

        return query_all

    @classmethod
    def get_query_agg(cls, field):
        query_agg = """
          "aggs": {
            "%s": {
              "terms": {
                "field": "%s",
                "size": %i,
                "order": {
                  "_count": "desc"
                }
              }
            }
          }
        """ % (cls.AGGREGATION_ID, field, cls.AGG_SIZE)

        return query_agg

    @classmethod
    def get_agg(cls, field, start = None, end = None):
        query_all = cls.get_query_all(start, end)
        query_agg = ElasticQuery.get_query_agg(field)

        query = """
            {
              "size": 0,
              %s,
              %s
              }
        """ % (query_agg, query_all)

        return query



class PeterAndTheWolf(AlertFromBuckets):

    def get_metrics_query(self):
        return ElasticQuery.get_agg("priority", self.start, self.end)

    def check(self):
        """
        Unbreak Now! should be always under 10%
        High tasks should be always under 25%

        :start Datetime from which start checking the metric
        :end   Datetime from which start checking the metric
        """
        ranges = {
            'Unbreak Now!': 10,
            'High': 25
        }
        self.check_metrics_ranges(ranges)


if __name__ == '__main__':
    peter = PeterAndTheWolf()
    peter.check()
