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

ES_URL = 'http://localhost:9200/maniphestMng/_search'

class Alert():

    def get_metrics_data(self):
        """ Get the metrics data from ES """
        # curl -XPOST "http://localhost:9200/maniphestMng/_search" -d'
        r = requests.post(ES_URL, self.QUERY)
        return r.json()

    def get_metrics(self):
        """ Get the metrics """
        raise NotImplementedError

    def check_metrics(self, query):
        """ Check that metrics are in the ranges """
        raise NotImplementedError


class PeterAndTheWolf(Alert):
    AGGREGATION_ID = "1"

    QUERY = """
        {
          "size": 0,
          "aggs": {
            "%s": {
              "terms": {
                "field": "priority",
                "size": 10,
                "order": {
                  "_count": "desc"
                }
              }
            }
          },
          "query": {
            "bool": {
              "must": [
                {
                  "query_string": {
                    "analyze_wildcard": true,
                    "query": "*"
                  }
                },
                {
                  "range": {
                    "metadata__updated_on": {
                      "gte": 1446609354540,
                      "lte": 1478231754540,
                      "format": "epoch_millis"
                    }
                  }
                }
              ]
            }
          }
        }
    """ % (AGGREGATION_ID)

    def get_metrics(self):
        """ Get the metrics """
        data = self.get_metrics_data()

        buckets = data['aggregations'][self.AGGREGATION_ID]['buckets']

        # Check all items are in the aggregations
        if data['aggregations'][self.AGGREGATION_ID]['sum_other_doc_count'] > 0:
            raise RuntimeError("Not all items in aggregations")

        return buckets

    def check_metrics(self):
        """
        Unbreak Now! should be always under 10%
        High tasks should be always under 25%
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
                        print("ALERT %s: %i > %i for %s " % (self.__class__.__name__, val, ranges[r], r))


if __name__ == '__main__':
    peter = PeterAndTheWolf()
    peter.check_metrics()
