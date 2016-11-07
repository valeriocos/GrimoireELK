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

import argparse
import json

import pytz
import requests

from datetime import datetime, timedelta

from dateutil import parser

# Default values so it works without params
ES_URL = 'http://localhost:9200'
ES_INDEX='maniphestMng'
ES_ALERTS_URL = ES_URL
ES_ALERTS_INDEX = 'alerts'
START = parser.parse('1900-01-01')
END = parser.parse('2100-01-01')

def get_now():
    # Return now datetime with timezome
    return datetime.utcnow().replace(tzinfo=pytz.utc)

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
    def get_query_agg_terms(cls, field):
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
    def get_query_agg_max(cls, field):
        query_agg = """
          "aggs": {
            "%s": {
              "max": {
                "field": "%s"
              }
            }
          }
        """ % (cls.AGGREGATION_ID, field)

        return query_agg


    @classmethod
    def get_count(cls, start = None, end = None):
        query_all = cls.get_query_all(start, end)

        query = """
            {
              "size": 0,
              %s
              }
        """ % (query_all)

        return query


    @classmethod
    def get_agg_count(cls, field, start=None, end=None, agg_type="terms"):
        query_all = cls.get_query_all(start, end)
        if agg_type == "terms":
            query_agg = ElasticQuery.get_query_agg_terms(field)
        elif agg_type == "max":
            query_agg = ElasticQuery.get_query_agg_max(field)
        else:
            RuntimeError("Aggregation of %s not supported", agg_type)

        query = """
            {
              "size": 0,
              %s,
              %s
              }
        """ % (query_agg, query_all)

        return query


class Alert():

    def __init__(self, es_url=ES_URL, es_index=ES_INDEX,
                 es_alerts_url=ES_URL, start=START, end=END):
        if not es_url:
            es_url=ES_URL
        if not es_index:
            es_index=ES_INDEX
        if not es_alerts_url:
            es_alerts_url=ES_URL
        self.start = start
        self.end = end
        self.es_url = es_url
        self.es_index = es_index
        self.es_alerts_url = es_alerts_url
        self.es_alerts_index = ES_ALERTS_INDEX

        self.__check_alerts_es()

    def __check_alerts_es(self):
        """ Check that the alert ES is ready to receive alerts """
        # Check that the index exists
        index_url = self.es_alerts_url+"/"+self.es_alerts_index
        r = requests.get(index_url)
        if r.status_code == 200:
            # Index exists
            return
        # Create the index with its mapping
        r = requests.put(index_url)
        # By default all strings are not analyzed
        url_map = index_url + "/items/_mappings"
        not_analyze_strings = """
        {
          "dynamic_templates": [
            { "notanalyzed": {
                  "match": "*",
                  "match_mapping_type": "string",
                  "mapping": {
                      "type":  "string",
                      "index": "not_analyzed"
                  }
               }
            }
          ]
        } """
        r = requests.put(url_map, data=not_analyze_strings)
        r.raise_for_status()

    def get_metrics_data(self):
        """ Get the metrics data from ES """
        # curl -XPOST "http://localhost:9200/maniphestMng/_search" -d'
        url = self.es_url+'/'+self.es_index+'/_search'
        query = self.get_metrics_query()
        r = requests.post(url, query)
        r.raise_for_status()
        return r.json()

    def alert2es(self, val, limit, field='', is_max=True, unit=''):
        """ Create a JSON with the alert and upload it to alerts ES """
        dt_now = get_now()
        alert = {
            "name": self.__class__.__name__,
            "metric": {
                "value": val,
                "unit": unit
            },
            "metric_limit":
            {
                "value": limit,
                "unit": unit
            },
            "field": field,
            "is_max": is_max,
            "@timestamp": dt_now.isoformat(),
            "origin": self.es_url,
            "index": self.es_index,
            "query": json.dumps(json.loads(self.get_metrics_query()))
        }
        url = self.es_alerts_url + "/" + self.es_alerts_index
        uid = alert["name"]+"_"+str(dt_now.timestamp())
        r = requests.post(url+"/items/"+uid, json.dumps(alert))
        r.raise_for_status()

    def get_metrics(self):
        """ Get the metrics """
        raise NotImplementedError

    def check_metrics(self, query):
        """ Check that metrics are in the ranges """
        raise NotImplementedError

    def get_metrics_query(self, query):
        """ Return the query to get the metrics """
        raise NotImplementedError

    def raise_alert(self, val, limit, field='', is_max=True, unit=''):
        """ Raise the alert

        :param val: value that raise the alert
        :param limit: limit value for the alert
        :param field: name of the field with the val
        :param is_max: if the limix is max or min
        :param unit: unit of the measure
        """
        self.alert2es(val, limit, field=field, is_max=is_max, unit=unit)

        if field != '':
            field = "for " + field
        op = "<"
        if is_max:
            op = ">"

        print("ALERT %s: %i%s %s %i%s %s (%s/%s, %s->%s)" %
              (self.__class__.__name__, val, unit, op, limit, unit, field,
               self.es_url, self.es_index, self.start, self.end))

class AlertFromBuckets(Alert):

    def get_metrics(self, agg_type='terms'):
        """ Get the metrics """
        data = self.get_metrics_data()

        if agg_type == 'terms':
            buckets = data['aggregations'][ElasticQuery.AGGREGATION_ID]['buckets']
            # Check all items are in the aggregations
            if data['aggregations'][ElasticQuery.AGGREGATION_ID]['sum_other_doc_count'] > 0:
                raise RuntimeError("Not all items in aggregations")
        elif agg_type == 'max':
            buckets = data['aggregations'][ElasticQuery.AGGREGATION_ID]["value_as_string"]
        else:
            RuntimeError("Aggregation of %s not supported", agg_type)

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
        total = 0
        metrics = self.get_metrics()
        for agg in metrics :
            total += agg['doc_count']

        # Check ranges
        for agg in metrics:
            for r in ranges:
                if agg['key'] == r:
                    val = agg['doc_count']/total*100
                    if val > ranges[r]:
                        self.raise_alert(val, ranges[r], r, unit='%')

class PeterAndTheWolf(AlertFromBuckets):

    def get_metrics_query(self):
        return ElasticQuery.get_agg_count("priority", self.start, self.end)

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

class Trends(Alert):

    PERIODS = {'day':1, 'week':7, 'month':30, 'year':365}
    DEFAULT_PERIOD = 'week'

    def get_metrics_query(self):
        return ElasticQuery.get_count(self.start, self.end)

    def check(self, period=DEFAULT_PERIOD, min_val=0, max_val=0):

        if period not in Trends.PERIODS.keys():
            raise RuntimeError('Period not supported: %s', period)

        offset = Trends.PERIODS[period]
        # Last interval metrics
        self.end = get_now()
        self.start = self.end - timedelta(days=offset)
        val_last_period = self.get_metrics_data()['hits']['total']
        # Previous interval metrics
        self.end = self.start
        self.start = self.end - timedelta(days=offset)
        val_previous_period = self.get_metrics_data()['hits']['total']
        trend = val_last_period - val_previous_period
        trend_percent = None
        if val_last_period == 0:
            if val_previous_period > 0:
                trend_percent = -100
            else:
                trend_percent = 0
        else:
            trend_percent = int((trend/val_last_period)*100)

        if trend_percent > max_val:
            self.raise_alert(trend_percent, max_val, field=period, unit='%')
        if trend_percent < min_val:
            self.raise_alert(trend_percent, min_val, field=period, is_max=False, unit='%')

class Freshness(AlertFromBuckets):

    AGG_TYPE = 'max'

    def get_metrics_query(self):
        return ElasticQuery.get_agg_count("metadata__updated_on", self.start, self.end, Freshness.AGG_TYPE)

    def check(self, max_days=0):
        """ Check that the data was updated before "days" ago """
        last_update = self.get_metrics(agg_type=Freshness.AGG_TYPE)
        last_update = parser.parse(last_update)
        freshness_days = (get_now() - last_update).days
        if freshness_days > max_days:
            self.raise_alert(freshness_days, max_days, unit='d')

def get_params():
    """Parse command line arguments"""

    parser = argparse.ArgumentParser()

    parser.add_argument('-g', '--debug', dest='debug',
                        action='store_true')
    parser.add_argument('--index', help="Index name to get alerts from")
    parser.add_argument('-e', '--elastic-url', help="Elastic URL to get alerts from")
    parser.add_argument('--elastic-alerts-url', help="Elastic URL to store alerts")

    # if len(sys.argv) == 1:
    #     parser.print_help()
    #     sys.exit(1)

    args = parser.parse_args()

    return args


if __name__ == '__main__':

    args = get_params()
    elastic = args.elastic_url
    elastic_index = args.index
    elastic_alerts = args.elastic_alerts_url

    freshness = Freshness(es_url=args.elastic_url, es_index=args.index,
                          es_alerts_url=args.elastic_alerts_url)
    freshness.check(1)
    peter = PeterAndTheWolf(es_url=args.elastic_url, es_index=args.index,
                            es_alerts_url=args.elastic_alerts_url)
    peter.check()
    trends = Trends(es_url=args.elastic_url, es_index=args.index,
                    es_alerts_url=args.elastic_alerts_url)
    trends.check(min_val=22)
    trends.check('day', min_val=5)
    trends.check('month', max_val=10)
    trends.check('year')
