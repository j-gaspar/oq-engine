# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright (C) 2015-2017 GEM Foundation
#
# OpenQuake is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenQuake. If not, see <http://www.gnu.org/licenses/>.

"""
Here there are some real functional tests starting an engine server and
running computations.
"""
from __future__ import print_function
import io
import os
import re
import sys
import json
import time
import unittest
import tempfile
import numpy
from django.test import Client
from openquake.baselib.general import writetmp, _get_free_port
from openquake.engine.export import core
from openquake.server.db import actions
from openquake.server.dbserver import db, get_status


class EngineServerTestCase(unittest.TestCase):
    hostport = 'localhost:%d' % _get_free_port()
    datadir = os.path.join(os.path.dirname(__file__), 'data')

    # general utilities

    @classmethod
    def post(cls, path, data=None):
        return cls.c.post('/v1/calc/%s' % path, data)

    @classmethod
    def post_nrml(cls, data):
        return cls.c.post('/v1/valid/', dict(xml_text=data))

    @classmethod
    def get(cls, path, **data):
        resp = cls.c.get('/v1/calc/%s' % path, data,
                         HTTP_HOST='127.0.0.1')
        if not resp.content:
            sys.stderr.write(open(cls.errfname).read())
            return {}
        try:
            return json.loads(resp.content.decode('utf8'))
        except:
            print('Invalid JSON, see %s' % writetmp(resp.content),
                  file=sys.stderr)
            return {}

    @classmethod
    def get_text(cls, path, **data):
        sc = cls.c.get('/v1/calc/%s' % path, data).streaming_content
        return b''.join(sc)

    @classmethod
    def wait(cls):
        # wait until all calculations stop
        while True:
            running_calcs = cls.get('list', is_running='true')
            if not running_calcs:
                break
            time.sleep(1)

    def postzip(self, archive):
        with open(os.path.join(self.datadir, archive), 'rb') as a:
            resp = self.post('run', dict(archive=a))
        try:
            js = json.loads(resp.content.decode('utf8'))
        except:
            raise ValueError(b'Invalid JSON response: %r' % resp.content)
        if resp.status_code == 200:  # ok case
            job_id = js['job_id']
            self.job_ids.append(job_id)
            time.sleep(1)  # wait a bit for the calc to start
            return job_id
        else:  # error case
            return ''.join(js)  # traceback string

    # start/stop server utilities

    @classmethod
    def setUpClass(cls):
        assert get_status() == 'running'
        cls.job_ids = []
        env = os.environ.copy()
        env['OQ_DISTRIBUTE'] = 'no'
        # let's impersonate the user openquake, the one running the WebUI:
        # we need to set LOGNAME on Linux and USERNAME on Windows
        env['LOGNAME'] = env['USERNAME'] = 'openquake'
        cls.fd, cls.errfname = tempfile.mkstemp(prefix='webui')
        print('Errors saved in %s' % cls.errfname, file=sys.stderr)
        cls.c = Client()

    @classmethod
    def tearDownClass(cls):
        cls.wait()
        os.close(cls.fd)

    # tests

    def test_404(self):
        # looking for a missing calc_id
        resp = self.c.get('/v1/calc/0')
        assert resp.status_code == 404, resp

    def test_ok(self):
        job_id = self.postzip('archive_ok.zip')
        self.wait()
        log = self.get('%s/log/:' % job_id)
        self.assertGreater(len(log), 0)
        results = self.get('%s/results' % job_id)
        self.assertGreater(len(results), 0)
        for res in results:
            for etype in res['outtypes']:  # test all export types
                text = self.get_text(
                    'result/%s' % res['id'], export_type=etype)
                print('downloading result/%s' % res['id'], res['type'], etype)
                self.assertGreater(len(text), 0)

        # test no filtering in actions.get_calcs
        all_jobs = self.get('list')
        self.assertGreater(len(all_jobs), 0)

        extract_url = '/v1/calc/%s/extract/' % job_id

        # check extract/composite_risk_model.attrs
        url = extract_url + 'composite_risk_model.attrs'
        self.assertEqual(self.c.get(url).status_code, 200)

        # check asset_values
        resp = self.c.get(extract_url + 'asset_values/0')
        data = b''.join(ln for ln in resp.streaming_content)
        got = numpy.load(io.BytesIO(data))  # load npz file
        self.assertEqual(len(got['array']), 0)  # there are 0 assets on site 0
        self.assertEqual(resp.status_code, 200)

        # check avg_losses-rlzs
        resp = self.c.get(
            extract_url + 'agglosses/structural?taxonomy=W-SLFB-1')
        data = b''.join(ln for ln in resp.streaming_content)
        got = numpy.load(io.BytesIO(data))  # load npz file
        self.assertEqual(len(got['array']), 1)  # expected 1 aggregate value
        self.assertEqual(resp.status_code, 200)

        # TODO: check aggcurves

        # there is some logic in `core.export_from_db` that it is only
        # exercised when the export fails
        datadir, dskeys = actions.get_results(db, job_id)
        # try to export a non-existing output
        with self.assertRaises(core.DataStoreExportError) as ctx:
            core.export_from_db(('XXX', 'csv'), job_id, datadir, '/tmp')
        self.assertIn('Could not export XXX in csv', str(ctx.exception))

    def test_classical(self):
        job_id = self.postzip('classical.zip')
        self.wait()

        # check that we get the expected outputs
        results = self.get('%s/results' % job_id)
        self.assertEqual(['fullreport', 'hcurves', 'hmaps', 'realizations',
                          'sourcegroups', 'uhs'], [r['name'] for r in results])

        # check the filename of the hmaps
        hmaps_id = results[2]['id']
        resp = self.c.head('/v1/calc/result/%s?export_type=csv' % hmaps_id)
        # remove output ID digits from the filename
        cd = re.sub(r'\d', '', resp._headers['content-disposition'][1])
        self.assertEqual(
            cd, 'attachment; filename=output--hazard_map-mean_.csv')

        # check oqparam
        resp = self.get('%s/oqparam' % job_id)  # dictionary of parameters
        self.assertEqual(resp['calculation_mode'], 'classical')

        # check the /extract endpoint
        url = '/v1/calc/%s/extract/hazard/rlzs' % job_id
        resp = self.c.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_err_1(self):
        # the rupture XML file has a syntax error
        job_id = self.postzip('archive_err_1.zip')
        self.wait()

        # download the datastore, even if incomplete
        resp = self.c.get('/v1/calc/%s/datastore' % job_id)
        self.assertEqual(resp.status_code, 200)

        tb = self.get('%s/traceback' % job_id)
        if not tb:
            sys.stderr.write('Empty traceback, please check!\n')

        self.post('%s/remove' % job_id)
        # make sure job_id is no more in the list of relevant jobs
        job_ids = [job['id'] for job in self.get('list', relevant=True)]
        self.assertFalse(job_id in job_ids)
        # NB: the job is invisible but still there

    def test_err_2(self):
        # the file logic-tree-source-model.xml is missing
        tb_str = self.postzip('archive_err_2.zip')
        self.assertIn('No such file', tb_str)

    def test_err_3(self):
        # there is no file job.ini, job_hazard.ini or job_risk.ini
        tb_str = self.postzip('archive_err_3.zip')
        self.assertIn('Could not find any file of the form', tb_str)

    def test_available_gsims(self):
        resp = self.c.get('/v1/available_gsims')
        self.assertIn(b'ChiouYoungs2014PEER', resp.content)

    # tests for nrml validation

    def test_validate_nrml_valid(self):
        valid_file = os.path.join(self.datadir, 'vulnerability_model.xml')
        with open(valid_file) as vf:
            valid_content = vf.read()
        resp = self.post_nrml(valid_content)
        self.assertEqual(resp.status_code, 200)
        resp_text_dict = json.loads(resp.content.decode('utf8'))
        self.assertTrue(resp_text_dict['valid'])
        self.assertIsNone(resp_text_dict['error_msg'])
        self.assertIsNone(resp_text_dict['error_line'])

    def test_validate_nrml_invalid(self):
        invalid_file = os.path.join(self.datadir,
                                    'vulnerability_model_invalid.xml')
        with open(invalid_file) as vf:
            invalid_content = vf.read()
        resp = self.post_nrml(invalid_content)
        self.assertEqual(resp.status_code, 200)
        resp_text_dict = json.loads(resp.content.decode('utf8'))
        self.assertFalse(resp_text_dict['valid'])
        self.assertIn(u'Could not convert lossRatio->positivefloats:'
                      ' float -0.018800826 < 0',
                      resp_text_dict['error_msg'])
        self.assertEqual(resp_text_dict['error_line'], 7)

    def test_validate_nrml_unclosed_tag(self):
        invalid_file = os.path.join(self.datadir,
                                    'vulnerability_model_unclosed_tag.xml')
        with open(invalid_file) as vf:
            invalid_content = vf.read()
        resp = self.post_nrml(invalid_content)
        self.assertEqual(resp.status_code, 200)
        resp_text_dict = json.loads(resp.content.decode('utf8'))
        self.assertFalse(resp_text_dict['valid'])
        self.assertIn(u'mismatched tag', resp_text_dict['error_msg'])
        self.assertEqual(resp_text_dict['error_line'], 9)

    def test_validate_nrml_missing_parameter(self):
        # passing a wrong parameter, instead of the required 'xml_text'
        resp = self.c.post('/v1/valid/', foo='bar')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.content,
                         b'Please provide the "xml_text" parameter')
