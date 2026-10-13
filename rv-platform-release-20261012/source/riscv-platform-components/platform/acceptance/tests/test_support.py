"""Regression checks for image identity and preservation of existing profiles."""
import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('support',pathlib.Path(__file__).resolve().parents[1]/'support.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Regression(unittest.TestCase):
    def test_resource_claim_uses_platform_group(self):
        self.assertEqual(module.resource_kind('resourceclaim'),'resourceclaims.platform.riscv.io')
        self.assertEqual(module.resource_kind('resourceclaims'),'resourceclaims.platform.riscv.io')
        self.assertEqual(module.resource_kind('pods'),'pods')

    def test_unknown_image_is_not_equated(self):
        self.assertNotEqual(module.canonical_image('repo@sha256:a'),module.canonical_image('sha256:b'))

    def test_proven_mapping_equates_manifest_and_config(self):
        aliases={'sha256:manifest':'sha256:config'}
        self.assertEqual(module.canonical_image('repo@sha256:manifest',aliases),module.canonical_image('sha256:config',aliases))

    def test_render_does_not_expand_application_shell(self):
        text='replicas: "@@MPI_WORKERS@@"\ncommand: ["$RV_MPI_SSH_ARGS", "@@MPI_RANKS@@"]\n'
        result=module.render(text,{'MPI_WORKERS':'2','MPI_RANKS':'4'})
        self.assertIn('replicas: 2',result)
        self.assertIn('"$RV_MPI_SSH_ARGS", "4"',result)

    def test_render_requires_all_values(self):
        with self.assertRaises(ValueError):module.render('@@UNSET@@',{})

    def test_existing_profile_keeps_pool_and_taints(self):
        with tempfile.TemporaryDirectory() as directory:
            t=module.Test.__new__(module.Test);t.node='worker';t.nonce='abcdef12';t.ns='test';t.directory=pathlib.Path(directory)
            t.values={};t.cleanups=[];t.node_obj={'spec':{}}
            original={'apiVersion':'platform.riscv.io/v1alpha1','kind':'NodeProfile','metadata':{'name':'production','namespace':'default','uid':'u1','resourceVersion':'7','annotations':{'keep':'yes'}},'spec':{'node':'worker','labels':{'platform.riscv.io/pool':'general'},'taints':[{'key':'platform.riscv.io/reserved','value':'yes','effect':'NoSchedule'}]}}
            state=copy.deepcopy(original)
            t.items=lambda *a,**kw:[copy.deepcopy(state)]
            t.get=lambda *a,**kw:copy.deepcopy(state)
            t.record=lambda *a,**kw:None
            t.wait=lambda *a,**kw:None
            t.render_file=lambda name:pathlib.Path(directory)/name
            desired={'metadata':{'annotations':{}},'spec':{'labels':{'platform.riscv.io/accept-abcdef12':'first'}}}
            t.cmd=lambda *a,**kw:json.dumps(desired)
            def patch(kind,current,fields):
                state.update(copy.deepcopy(fields))
            t.patch_guarded=patch
            t.prepare_profile()
            self.assertEqual(t.values['PROFILE_NAME'],'production')
            self.assertEqual(t.values['PROFILE_NS'],'default')
            t.apply_profile('profile.yaml')
            self.assertEqual(state['spec']['labels']['platform.riscv.io/pool'],'general')
            self.assertEqual(state['spec']['taints'],original['spec']['taints'])
            self.assertEqual(state['metadata']['annotations'],{'keep':'yes'})
            t.cleanups[0]()
            self.assertEqual(state['spec'],original['spec'])

    def test_conflicting_profile_authorization_is_not_overwritten(self):
        t=module.Test.__new__(module.Test);t.node='worker';t.nonce='abcdef12';t.ns='test'
        t.items=lambda *a,**kw:[{'spec':{'node':'worker'},'metadata':{'annotations':{'platform.riscv.io/allow-scenario':'existing-policy'}}}]
        with self.assertRaises(module.Skip):t.prepare_profile(scenario=True)

    def test_duplicate_node_profiles_are_rejected(self):
        t=module.Test.__new__(module.Test);t.node='worker';t.nonce='abcdef12';t.ns='test'
        t.items=lambda *a,**kw:[{'spec':{'node':'worker'}}]*2
        with self.assertRaises(module.Skip):t.prepare_profile()

if __name__=='__main__':unittest.main()
