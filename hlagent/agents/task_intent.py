"""Recognize clear task and question boundaries, never files or approvals."""

import re


TASK_ROUTES = {
    'imputation_inputs': ('imputation', 'imputation'),
    'hped_conversion': ('conversion', 'convert'),
    'prepare_phenotype': ('conversion', 'phenotype'),
    'prepare_summary': ('conversion', 'summary'),
    'phase_inputs': ('phasing', 'phasing'),
    **{f'node{n}_inputs': ('finemapping', f'node{n}-custom') for n in range(1, 6)},
}

def current_model_question(text):
    """Recognize session identity queries, never model recommendations or edits."""
    text = re.sub(r'\s+', ' ', text.casefold()).strip()
    text = re.sub(r'^[\x00-\x1fㄱ-ㆎ]+', '', text)
    if not re.search(r'\b(?:llm|model|provider)\b|모델|제공자', text):
        return False
    if re.search(r'\b(?:hla|hatk|node[1-5]|phasing|beagle|regression|logistic|statistical|association|imputation|can|could)\b|통계|회귀|분석', text):
        return False
    # Editing, navigation and hypothetical comparisons stay with the router.
    if re.search(r'\b(?:change|switch|set|configure|recommend|should|best|compare|difference|if)\b|'
                 r'바꿔|변경|설정|추천|차이|바꾸|바꿀', text):
        return False
    return bool(re.search(
        r'\b(?:what|which)\s+(?:is\s+)?(?:the\s+)?(?:(?:current|active|llm)\s+)*'
        r'(?:model|provider)(?:\s+(?:now|currently|is active|is current|are (?:you|we) using|am i using))?\s*[?.!]*$|'
        r'\b(?:show|tell|display)\b.*\b(?:current|active)\b.*\b(?:model|llm|provider)\b|'
        r'\b(?:current|active)\s+(?:llm\s+)?(?:model|provider)\s*\??$|'
        r'(?:llm|모델|제공자).*(?:뭐|무엇|어떤|알려|보여)|'
        r'(?:어떤|무슨).*(?:모델|llm).*(?:사용|쓰|이야|인가)', text))


def model_recommendation_question(text):
    """Keep clear LLM choice advice separate from requests to edit settings."""
    text = text.casefold()
    if not re.search(r'\b(?:llm|model|provider)\b|모델|제공자', text):
        return False
    if re.search(r'\b(?:node[1-5]|hatk|regression|statistical)\b|통계|회귀', text):
        return False
    return bool(re.search(r'\brecommend\w*\b|\b(?:which|what)\s+(?:llm\s+)?model\s+should\b|'
                          r'\b(?:which|what)\s+(?:llm\s+)?model\s+is\s+best\b|'
                          r'추천|(?:어떤|무슨).*모델.*(?:좋|쓸|써야|쓰면|사용하면)', text))


def explicit_task(text):
    """Return a narrow single-operation intent, or defer to contextual routing."""
    text = re.sub(r'\s+', ' ', text.casefold()).strip()
    # A clear covariate-adjusted association is Node2, not marker conditioning.
    if ((re.match(r'^(?:please\s+)?(?:test|run|perform)\b', text)
             or re.search(r'하고\s*싶|해\s*줘|해주세요', text))
            and re.search(r'\bassociations?(?:\b|(?=[가-힣]))|연관분석|연관\s*분석', text)
            and re.search(r'\b(?:age|sex|covariates?|ancestry|principal components)\b|공변량|나이|성별|주성분', text)
            and not re.search(r'\b(?:not|without|conditional|condition|stepwise|continuous|quantitative|then|first|example|phasing|omnibus|meta|rs\d+)\b|조건부|하지\s*마|않|먼저|그다음', text)):
        return 'node2_inputs'
    # Explanations, negation, unavailable methods, examples and explicit chains
    # need contextual interpretation; matching a tool name alone is insufficient.
    if re.search(r'\b(explain|describe|difference|meaning|whether|not|never|without|don\x27t|do\s+not|'
                 r'first|then|afterward|example|demo|covariates?|age|sex|ancestry|continuous|quantitative|stepwise|overlap\w*|'
                 r'memory|seed|threads?|processes|'
                 r'random.effects|same cohort|same study)\b|설명|차이|하지\s*마|않|예제|먼저|그다음', text):
        return None
    if re.search(r'\b(?:three|four|five|six|seven|eight|nine|ten|several|many)\s+(?:independent\s+)?studies\b|\b\d+\s*(?:gb|gib|g)\b', text):
        return None
    if any(int(n) != 2 for n in re.findall(r'\b(\d+)\s+(?:(?:independent|compatible)\s+)*studies\b', text)):
        return None
    if re.search(r'\band\s+(?:also\s+)?(?:run|perform|phase|impute|convert|prepare|generate|create|combine|pool|merge|phasing|imputation|conversion)\b', text):
        return None
    if re.match(r'^(what|why|how|when|which|does|is|are|should|do i|would it)\b', text):
        return None
    request = re.search(r'\b(?:want to|wanna|would like to|need to|can you|could you|please)\s+(.+)', text)
    focus = request.group(1) if request else text
    verbs = r'(?:impute|infer|create|generate|build|test|run|perform|conduct|adjust|condition|phase|combine|pool|merge|prepare|convert|normalize|format)'
    english_request = re.match(verbs + r'\b', focus)
    korean_request = re.search(r'하고\s*싶|만들고\s*싶|준비하고\s*싶|변환하고\s*싶|합치고\s*싶|보정하고\s*싶|해\s*줘|해주세요', text)
    if not english_request and not korean_request:
        return None
    # Downstream purpose is not another requested task. Existing input state is
    # retained in text for distinguishing already-phased AA inputs.
    focus = re.split(r';|\bso (?:that )?i can\b|\bfor (?:later |an? |the )?(?:omnibus|meta.analysis|association|fine.mapping)', focus)[0]
    def has(pattern):
        return re.search(pattern, focus) is not None
    prep = has(r'\b(prepare|convert|normalize|format)\b|준비|변환|가공')
    hits = []
    if prep and has(r'\b(phenotype|clinical)\b|임상|표현형'):
        hits.append('prepare_phenotype')
    if prep and has(r'\b(summary|summaries|association)\b|요약통계|요약\s*통계'):
        hits.append('prepare_summary')
    if prep and has(r'\bhped\b') and has(r'\bcookhla\b|allele'):
        hits.append('hped_conversion')
    if has(r'\b(impute|imputation)\b|imputation을|대치') and re.search(r'\b(hla|cookhla)\b', text):
        hits.append('imputation_inputs')
    if has(r'\bphase\b|phasing을|phasing\s*해|페이징|페이징을') and re.search(r'markers?|genotypes?|\b(hla|hatk|panel)\b|마커', text):
        hits.append('phase_inputs')
    if (has(r'\b(create|generate|build)\b|생성|만들') and has(r'\bmarkers?\b|마커')
            and re.search(r'\b(hla|hatk|hped)\b', text)):
        hits.append('node1_inputs')
    if not prep:
        conditional = has(r'\b(adjust|condition|conditional|conditioning)\b|조건부|보정')
        association = has(r'\b(associations?|tests?|omnibus)\b|연관|검정')
        aa = re.search(r'amino.acid|\baa\b|아미노산', text) is not None
        if conditional and has(r'\bmarkers?\b|marker[를을]|마커') and association:
            hits.append('node3_inputs')
        elif association and (has(r'\bomnibus\b') or aa and has(r'positions?|위치')):
            hits.append('node4_inputs')
        elif has(r'\b(combine|pool|merge)\b|합치|통합') and has(r'\b(studies|study|results|summaries)\b|연구|결과'):
            hits.append('node5_inputs')
        elif has(r'\b(test|run|perform|conduct)\b|연관|검정') and association:
            hits.append('node2_inputs')
    # Input preparation for a later analysis takes priority only when it is the
    # single operation; distinct operations still require clarification.
    return hits[0] if len(hits) == 1 else None


def question_destination(text):
    """Resolve clear explanation/navigation boundaries after question classification.

    None leaves contextual or unclear questions to the model. This function never
    classifies a task or changes the destination of an explicit execution request.
    """
    text = re.sub(r'\s+', ' ', text.casefold()).strip()
    navigation = re.search(r'\b(commands?|menus?|settings?|config|provider|api key|back|cancel|exit)\b|'
                 r'\bwhat can (?:hlagent|you|this (?:app|program|tool|role)) do\b|'
                 r'\b(?:which|what) (?:task|analysis|step).*(?:choose|select|next|run)\b|'
                 r'\bhow (?:do|can) i (?:run|start|open|return|change|select|choose)\b|'
                 r'\bwhat (?:should|do|can) i (?:type|enter)\b|'
                 r'어떤\s*기능|명령어|메뉴|설정|돌아가|다음.*단계', text)
    topic = re.search(r'\b(?:hla|hped|imgt|node[1-5]|imputation|phasing|association|conditional|'
                      r'omnibus|haplotypes?|alleles?|amino.acid|genotypes?|phenotypes?|'
                      r'genome build|odds ratio|p.value)(?:\b|(?=[가-힣]))|'
                      r'아미노산|조건부|연관분석|대립유전자|유전자형', text)
    explanation = re.search(r'\b(?:explain|difference|meaning|means|interpret|causal|dependence|'
                            r'independence|why)\b|\bwhat (?:is|are)\b|'
                            r'\b(?:what|which) (?:inputs?|files?)\b|'
                            r'\bhow (?:does|do).*(?:work|differ)\b|'
                            r'\bdoes.*(?:adjust|require|account|correct)\b|'
                            r'설명|차이|의미|해석|왜|어떤\s*입력|의존성|인과', text)
    if navigation:
        command_lookup = re.search(r'\b(?:which|what) (?:is |are )?(?:the )?commands?\b|'
                                   r'\bhow (?:do|can) i (?:run|start|open)\b|'
                                   r'\bwhat (?:should|do|can) i (?:type|enter)\b', text)
        mixed_explanation = re.search(r'\b(?:difference|meaning|means|interpret|why)\b|'
                                      r'\bexplain\b.*\band\b|차이|의미|해석|왜', text)
        if command_lookup and not mixed_explanation:
            return 'answer'
        return None if topic and explanation else 'answer'
    return 'tutor' if topic and explanation else None


def command_guidance(question):
    """Provide command help without trusting a model's internal action spelling."""
    if question_destination(question) != 'answer':
        return None
    text = question.casefold()
    if not re.search(r'\bcommands?\b|\bhow (?:do|can) i (?:run|start|open|change)\b|'
                     r'\bwhat (?:should|do|can) i (?:type|enter)\b|명령어', text):
        return None
    if re.search(r'\b(?:not|never|avoid|don\x27t)\b|하지\s*마|않', text):
        return 'At Supervisor, type help to see the available commands and shortcuts.'
    targets = []
    for pattern, command in (
        (r'\b(?:phasing|phase|phase_inputs)\b|페이징', 'phasing'),
        (r'\b(?:imputation|impute|cookhla|imputation_inputs)\b', 'imputation'),
        (r'\b(?:phenotype|prepare_phenotype)\b|임상', 'prepare-phenotype'),
        (r'\b(?:summary|summaries|prepare_summary)\b|요약', 'prepare-summary'),
        (r'\b(?:hped|convert|conversion|hped_conversion)\b', 'convert'),
        (r'\b(?:hatk|fine.mapping)\b', 'hatk'),
        (r'\b(?:tutor|learn)\b', 'tutor'),
        (r'\b(?:config|settings|provider|model|llm)\b|설정', 'config'),
        (r'\b(?:resume|recover)\b|복구', 'resume'),
        (r'\b(?:report|html)\b|보고서', 'report'),
        (r'\bworkflow\b', 'workflow'),
        (r'\b(?:next|continue)\b', 'next'),
        (r'\bhelp\b', 'help'),
        (r'\bback\b|돌아가', 'back'),
        (r'\b(?:exit|quit)\b|종료', 'exit'),
    ):
        if re.search(pattern, text):
            targets.append(command)
    for n in range(1, 6):
        if re.search(rf'\bnode{n}(?:_inputs|_custom|-custom)?\b', text):
            return (f'At Supervisor, type node{n}-custom for your own inputs, or node{n} for the bundled example.\n'
                    'Review and approve a plan with y before analysis starts.')
    if len(set(targets)) == 1:
        return 'At Supervisor, type ' + targets[0] + '.'
    return 'At Supervisor, type help to see the available commands and shortcuts.'
