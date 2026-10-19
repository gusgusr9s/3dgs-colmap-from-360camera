import unittest

from app.pipeline.camera_spacing import spacing_report, compare_spacing


class CameraSpacingTests(unittest.TestCase):
    def example(self, scale=1, step=.03):
        cameras=[dict(position=[i*step*scale,0,0],frame_index=i*8) for i in range(12)]
        preview=dict(cameras=cameras,summary={},unregistered_frames=[])
        geometry=dict(depths={str(c['frame_index']):scale for c in cameras},
                      overlap={str(i):dict(p10_common=200,p10_fraction=.4) for i in range(1,9)})
        reference=dict(job='cbf586354f',mean=10,raw_mean=.1)
        return preview,geometry,reference

    def test_mean_reference_and_independent_reconstruction_scale(self):
        a=compare_spacing(*self.example(),sample_every=8,fps=30)
        b=compare_spacing(*self.example(scale=120),sample_every=8,fps=30)
        self.assertAlmostEqual(a['mean'],.03)
        self.assertAlmostEqual(a['ratio_to_reference'],.3)
        self.assertAlmostEqual(a['ratio_to_reference'],b['ratio_to_reference'])
        self.assertTrue(all(s['density_state']=='very_dense' for s in a['segments']))
        self.assertEqual(a['recommendation']['sample_every'],10)
        self.assertAlmostEqual(a['recommendation']['gap_seconds'],1/3)
        self.assertEqual(a['recommendation']['basis'],'sampling_target')

    def test_insufficient_overlap_or_missing_poses_prevents_increase(self):
        p,g,r=self.example()
        g['overlap']={}
        self.assertEqual(compare_spacing(p,g,r,8)['recommendation']['direction'],'inspect')
        p,g,r=self.example()
        p['unregistered_frames']=[56]
        self.assertEqual(compare_spacing(p,g,r,8)['recommendation']['sample_every'],8)
        p['unregistered_frames']=[];p['summary']['suspicious_segments']=1
        self.assertEqual(compare_spacing(p,g,r,8)['recommendation']['direction'],'inspect')

    def test_wide_spacing_recommends_smaller_interval(self):
        p,g,r=self.example(step=.3)
        a=compare_spacing(p,g,r,30,30)
        self.assertEqual(a['recommendation']['direction'],'decrease')
        self.assertLess(a['recommendation']['sample_every'],30)
        self.assertTrue(all(s['density_state']=='wide' for s in a['segments']))
        self.assertEqual(compare_spacing(p,g,r,1,30)['recommendation']['direction'],'inspect')

    def test_missing_geometry_or_reference_has_explicit_fallback(self):
        p,g,r=self.example()
        self.assertIn('comparison_unavailable',compare_spacing(p,{},r))
        self.assertEqual(compare_spacing(p,g,None)['recommendation']['basis'],'track_overlap')
        p,g,r=self.example(step=0)
        self.assertEqual(compare_spacing(p,g,r,8)['recommendation']['direction'],'inspect')

    def test_reference_advice_scales_with_source_fps(self):
        for fps, limit in ((24,8),(29.97,10),(30,10),(60,20)):
            with self.subTest(fps=fps):
                recommendation=compare_spacing(*self.example(),sample_every=8,fps=fps)['recommendation']
                self.assertEqual(recommendation['sample_every'],limit)
                self.assertEqual(recommendation['direction'],'keep' if limit==8 else 'increase')

    def test_overlap_advice_does_not_restore_one_second_interval(self):
        for fps, current, limit in ((30,30,10),(60,60,20)):
            for fraction in (.4,.9):
                p,g,_=self.example()
                g['overlap']['1']['p10_fraction']=fraction
                recommendation=compare_spacing(p,g,None,current,fps)['recommendation']
                self.assertEqual(recommendation['sample_every'],limit)
                self.assertEqual(recommendation['direction'],'decrease')
                self.assertEqual(recommendation['basis'],'sampling_target')
                self.assertAlmostEqual(recommendation['gap_seconds'],1/3)

    def test_overlap_is_checked_at_the_final_suggested_interval(self):
        p,g,r=self.example()
        g['overlap']['4']=dict(p10_common=10,p10_fraction=.01)
        recommendation=compare_spacing(p,g,r,8,30)['recommendation']
        self.assertEqual(recommendation['sample_every'],10)
        self.assertEqual(recommendation['direction'],'increase')
        self.assertEqual(recommendation['overlap'],g['overlap']['2'])

    def test_low_overlap_can_still_request_denser_than_source_default(self):
        p,g,_=self.example()
        g['overlap']['1']=dict(p10_common=40,p10_fraction=.05)
        recommendation=compare_spacing(p,g,None,10,30)['recommendation']
        self.assertEqual(recommendation['sample_every'],5)
        self.assertEqual(recommendation['direction'],'decrease')
        self.assertEqual(recommendation['basis'],'low_track_overlap')

    def test_pose_issues_still_require_inspection_with_sampling_target(self):
        p,g,_=self.example()
        p['summary']['suspicious_segments']=2
        recommendation=compare_spacing(p,g,None,30,30)['recommendation']
        self.assertEqual(recommendation['direction'],'inspect')
        self.assertEqual(recommendation['basis'],'pose_quality')
        self.assertEqual(recommendation['sample_every'],10)

    def test_scale_independent_report_and_counts(self):
        cameras = [{'position':[x,0,0]} for x in (0,.001,.2,1)]
        report = spacing_report(cameras, .15, 30)
        self.assertEqual([s['state'] for s in report['segments']],['dense','within_range','wide'])
        self.assertAlmostEqual(report['minimum'],.001)
        scaled = spacing_report([{'position':[v*100 for v in c['position']]} for c in cameras], .15, 30)
        self.assertAlmostEqual(report['median_percent'],scaled['median_percent'])

    def test_repeated_centers_and_short_trajectories(self):
        self.assertTrue(spacing_report([{'position':[0,0,0]}]*3)['degenerate'])
        self.assertIsNone(spacing_report([]))
        self.assertIsNone(spacing_report([{'position':[0,0,0]}]))
        with self.assertRaises(ValueError):
            spacing_report([],1,.1)

    def test_untrusted_invalid_centers_rejected(self):
        with self.assertRaises(ValueError):
            spacing_report([{'position':[0,0,0]}, {'position':[float('nan'),0,0]}])


if __name__ == '__main__':
    unittest.main()
