"""部委检测 v3：新机构词表 + 联合发文显式检测。"""

from conftest import make_article

from agent.tools.ministry_signals import (
    detect_ministries,
    detect_joint_issuance,
    _parse_cn_number,
)


class TestParseCnNumber:
    def test_digits(self):
        assert _parse_cn_number('14') == 14

    def test_simple(self):
        assert _parse_cn_number('三') == 3

    def test_ten(self):
        assert _parse_cn_number('十') == 10

    def test_ten_plus(self):
        assert _parse_cn_number('十四') == 14

    def test_tens(self):
        assert _parse_cn_number('二十') == 20


class TestJointIssuance:
    def test_explicit_joint_document(self):
        art = make_article(
            title='十部门联合印发整治通知',
            content='国家卫生健康委等十部门联合印发通知，部署集中整治工作。',
        )
        result = detect_joint_issuance([art])
        assert result['found']
        assert result['max_departments'] == 10

    def test_arabic_numeral(self):
        art = make_article(content='市场监管总局等14部门联合开展专项行动。')
        result = detect_joint_issuance([art])
        assert result['max_departments'] == 14

    def test_huitong_pattern(self):
        art = make_article(content='央行会同有关部门开展整治。')
        result = detect_joint_issuance([art])
        assert result['found']

    def test_no_joint(self):
        art = make_article(content='某地推进乡村振兴工作。')
        result = detect_joint_issuance([art])
        assert not result['found']


class TestMinistryV3:
    def test_new_2023_organs_detected(self):
        art = make_article(
            title='金融监管总局部署风险排查',
            content='国家金融监督管理总局、国家数据局联合发布指引。',
            page_no=2,
        )
        result = detect_ministries([art])
        assert '国家金融监督管理总局' in result['ministries_found']
        assert '国家数据局' in result['ministries_found']

    def test_discipline_triggers_l5(self):
        art = make_article(
            title='中央纪委部署医药领域集中整治',
            content='中央纪委国家监委召开动员会，部署医药领域腐败问题集中整治。',
            page_no=1,
        )
        result = detect_ministries([art])
        assert result['has_discipline']
        assert result['coordination_level'] == 'L5'

    def test_central_commission_triggers_l4(self):
        art = make_article(
            title='中央金融委员会研究部署金融风险防范',
            content='中央金融委会议强调防范化解金融风险。',
            page_no=1,
        )
        result = detect_ministries([art])
        assert result['coordination_level'] == 'L4'

    def test_low_weight_discipline_mention_does_not_trigger_l5(self):
        art = make_article(
            content='文末顺带提到纪检监察机关的一般性工作。',
            page_no=9,
        )
        result = detect_ministries([art])
        assert result['coordination_level'] != 'L5'

    def test_joint_issuance_floors_level_at_l2(self):
        # 只点名一个部委，但出现"等五部门联合印发"——协调已完成
        art = make_article(
            title='五部门联合印发新规',
            content='教育部等五部门联合印发文件，规范校外培训。',
            page_no=2,
        )
        result = detect_ministries([art])
        assert result['joint_issuance']['found']
        assert result['coordination_level'] == 'L2'
        assert result['time_compression'] <= 0.8
