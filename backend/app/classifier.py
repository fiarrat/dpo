"""
Детерминированный классификатор обращений.

Работает без ИИ и без сети: взвешенные регулярные выражения по русскому тексту.
ИИ (llm.py) подключается сверху и может уточнить результат, но никогда не
является единственным источником — правила остаются страховкой и дают
объяснимость: каждое решение сопровождается списком сработавших сигналов.

Морфология обрабатывается «обрубками» основ (напр. `уточн\\w*`), этого
достаточно для юридических формулировок, которые довольно шаблонны.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .domain import (
    Flag, NON_PD_TYPES, RKN_TYPES, RequesterKind, RequestType, SubjectType,
)

# --------------------------------------------------------------------------- #
#  Нормализация
# --------------------------------------------------------------------------- #

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = (text or "").lower().replace("ё", "е").replace("\xa0", " ")
    return _WS.sub(" ", t)


@dataclass
class Signal:
    """Одно сработавшее правило — попадает в интерфейс как обоснование."""
    kind: str          # TYPE | REQUESTER | SUBJECT | FLAG | ENTITY | SERVICE
    key: str
    weight: float
    matched: str
    where: str = "body"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "key": self.key, "weight": round(self.weight, 2),
            "matched": self.matched[:200], "where": self.where,
        }


@dataclass
class FlagProposal:
    level: Flag
    code: str
    reason: str

    def to_dict(self) -> dict:
        return {"level": self.level.value, "code": self.code, "reason": self.reason}


@dataclass
class Classification:
    request_type: RequestType = RequestType.UNCLASSIFIED
    secondary_types: list[RequestType] = field(default_factory=list)
    requester_kind: RequesterKind = RequesterKind.UNKNOWN
    subject_type: SubjectType = SubjectType.UNKNOWN
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    flags: list[FlagProposal] = field(default_factory=list)
    legal_entity_mentioned: str = ""
    legal_entity_id: int | None = None
    service_id: int | None = None
    service_mentioned: str = ""
    summary: str = ""
    #: Найденные в тексте реквизиты — подсказки для карточки.
    extracted: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "request_type": self.request_type.value,
            "secondary_types": [t.value for t in self.secondary_types],
            "requester_kind": self.requester_kind.value,
            "subject_type": self.subject_type.value,
            "confidence": round(self.confidence, 3),
            "scores": {k: round(v, 2) for k, v in sorted(
                self.scores.items(), key=lambda kv: -kv[1]) if v > 0},
            "signals": [s.to_dict() for s in self.signals],
            "flags": [f.to_dict() for f in self.flags],
            "legal_entity_mentioned": self.legal_entity_mentioned,
            "service_mentioned": self.service_mentioned,
            "summary": self.summary,
            "extracted": self.extracted,
        }


# --------------------------------------------------------------------------- #
#  Правила: тип обращения
# --------------------------------------------------------------------------- #
#  (регулярное выражение, вес). Вес 3+ — почти однозначный маркер,
#  1-2 — вспомогательный.

TYPE_PATTERNS: dict[RequestType, list[tuple[str, float]]] = {
    RequestType.ACCESS: [
        (r"предостав\w+ (?:мне |копи\w+ )?(?:сведени|информаци)\w* о (?:моих )?персональн\w+ данн\w+", 4),
        (r"ознаком\w+ (?:меня )?с (?:моими )?персональн\w+ данн\w+", 4),
        (r"(?:прошу|требую|запрашива\w+) предостав\w+ .{0,60}персональн\w+ данн\w+", 3.5),
        (r"перечень (?:моих )?(?:обрабатываемых )?персональн\w+ данн\w+", 3),
        (r"част\w* 7 стать\w* 14|ч\.\s?7 ст\.\s?14|ст\.\s?14 .{0,20}ч\.\s?7", 4),
        (r"стать\w* 14 федеральн\w+ закон|ст\.\s?14 (?:фз[- ]?152|152[- ]?фз)", 3),
        (r"право на доступ к (?:своим |моим )?персональн\w+ данн\w+", 3.5),
        (r"копи\w+ (?:моих )?персональн\w+ данн\w+", 3),
        (r"источник\w* получени\w* (?:моих )?персональн\w+ данн\w+", 2.5),
        (r"сроки обработк\w+ .{0,30}персональн\w+ данн\w+", 2),
        (r"правов\w+ основани\w+ .{0,40}обработк\w+", 2),
    ],
    RequestType.CONFIRM_PROCESSING: [
        (r"подтверд\w+ факт\w* обработк\w+ (?:моих )?персональн\w+ данн\w+", 4),
        (r"обрабатыва\w+ ли (?:вы|компания|общество) (?:мои|моих) персональн\w+ данн\w+", 4),
        (r"наличи\w+ (?:у вас )?(?:моих )?персональн\w+ данн\w+", 3),
        (r"имеются ли у вас (?:мои )?персональн\w+ данн\w+", 3.5),
    ],
    RequestType.RECTIFICATION: [
        (r"(?:прошу|требую) (?:уточнить|исправить|актуализировать|скорректировать)", 3.5),
        (r"уточн\w+ (?:мои )?персональн\w+ данн\w+", 4),
        (r"(?:неточн|недостоверн|неполн|неактуальн)\w+ (?:персональн\w+ )?данн\w+", 3.5),
        (r"указан\w+ (?:неверн|ошибочн|некорректн)\w+", 3),
        (r"опечатк\w+ в (?:фамили|имен|отчеств|дат|документ)\w+", 3),
        (r"стать\w* 21|ст\.\s?21", 1.5),
        (r"смен\w+ (?:фамили|имен|отчеств)\w+", 2.5),
    ],
    RequestType.BLOCKING: [
        (r"(?:прошу|требую) (?:за)?блокирова\w+", 4),
        (r"блокирован\w+ (?:моих )?персональн\w+ данн\w+", 4),
        (r"приостанов\w+ обработк\w+ (?:моих )?персональн\w+ данн\w+", 3.5),
    ],
    RequestType.ERASURE: [
        (r"(?:прошу|требую) (?:уничтожить|удалить|стереть)", 3.5),
        (r"уничтож\w+ (?:мои|моих) персональн\w+ данн\w+", 4.5),
        (r"удал\w+ (?:мои|моих|все мои) персональн\w+ данн\w+", 4.5),
        (r"удал\w+ (?:мою )?учетн\w+ запис\w+|удал\w+ (?:мой )?аккаунт|удал\w+ (?:мой )?профил\w+", 3),
        (r"право на забвение", 3),
        (r"прекрат\w+ хранени\w+ (?:моих )?данн\w+", 3),
        (r"ст\.?\s?21 .{0,25}(?:ч\.?\s?4|част\w* 4)", 3),
    ],
    RequestType.CONSENT_WITHDRAWAL: [
        (r"отзыва?\w* сво\w* согласи\w*", 5),
        (r"отзыв\w* согласи\w* на обработк\w+", 5),
        (r"(?:настоящим )?отзыва\w+ соглас\w+", 4.5),
        (r"прош\w+ (?:считать )?согласи\w+ .{0,25}отозванн\w+", 4),
        (r"более не соглас\w+ на обработк\w+", 3.5),
        (r"ст\.?\s?9 .{0,25}(?:ч\.?\s?2|част\w* 2)", 2.5),
    ],
    RequestType.STOP_MARKETING: [
        (r"прекрат\w+ .{0,40}(?:рассылк|реклам|продвижен|маркетинг)\w+", 4),
        (r"отписа\w+ .{0,20}(?:от )?(?:рассылк|реклам)\w+", 3.5),
        (r"не (?:желаю|хочу) получать (?:реклам|рассылк|смс|сообщени)\w+", 3.5),
        (r"в целях продвижени\w+ товар\w+", 4),
        (r"ст\.?\s?15 .{0,25}(?:ч\.?\s?2|част\w* 2)", 3),
        (r"спам[- ]?(?:звонк|рассылк|смс)\w*", 2.5),
    ],
    RequestType.UNLAWFUL_PROCESSING: [
        (r"неправомерн\w+ обработк\w+", 4.5),
        (r"незаконн\w+ (?:обработк|сбор|получен|распространен)\w+ .{0,30}данн\w+", 4.5),
        (r"без моего согласи\w+", 3.5),
        (r"утечк\w+ (?:персональн\w+ )?данн\w+", 4),
        (r"мои данные (?:оказались|попали|переданы) (?:в|третьим)", 3.5),
        (r"наруш\w+ (?:требовани\w+ )?(?:фз[- ]?152|152[- ]?фз|закон\w* о персональн\w+ данн\w+)", 3.5),
        (r"откуда (?:у вас|вы (?:взяли|получили)) мои (?:персональн\w+ )?данн\w+", 3),
    ],
    RequestType.AUTOMATED_DECISION: [
        (r"автоматизированн\w+ (?:обработк|принят\w+ решени)\w+", 4),
        (r"решени\w+ принят\w+ .{0,30}автоматическ\w+", 4),
        (r"скоринг|алгоритм принял решени|отказ\w* автоматическ\w+", 3),
        (r"ст\.?\s?16", 2.5),
    ],
    RequestType.CROSS_BORDER_INFO: [
        (r"трансграничн\w+ передач\w+", 4.5),
        (r"передач\w+ .{0,30}(?:за (?:границу|рубеж)|в иностранн\w+)", 3),
        (r"ст\.?\s?12 .{0,20}(?:фз[- ]?152|152[- ]?фз)", 2.5),
    ],
    RequestType.SUBJECT_COMPLAINT: [
        (r"жалоб\w+ на (?:обработк|действи)\w+", 3),
        (r"выража\w+ (?:свое )?несоглас\w+", 2),
        (r"претензи\w+ .{0,40}персональн\w+ данн\w+", 3),
    ],

    # --- Роскомнадзор ------------------------------------------------------ #
    RequestType.RKN_INFO_REQUEST: [
        (r"запрос\w* информаци\w+", 3),
        (r"необходим\w+ информаци\w+ .{0,40}(?:уполномоченн\w+ орган|роскомнадзор)", 4),
        (r"ст\.?\s?20 .{0,25}(?:ч\.?\s?4|част\w* 4)", 4.5),
        (r"(?:прошу|просим) (?:вас )?(?:в срок|представить|предоставить) .{0,60}(?:сведени|информаци|документ)\w+", 2.5),
        (r"в течение дес\w+ рабочих дней", 2.5),
    ],
    RequestType.RKN_SUBJECT_COMPLAINT: [
        (r"поступил\w* обращени\w+ (?:гр\w*|гражданин\w*|субъект\w+)", 4.5),
        (r"обращени\w+ .{0,30}(?:гр\.|гражданин)", 3.5),
        (r"по вопросу (?:нарушени\w+ )?(?:прав\w* )?субъект\w+ персональн\w+ данн\w+", 4),
        (r"направляем .{0,30}обращени\w+ .{0,30}(?:для рассмотрени|по компетенц)\w+", 4),
        (r"довод\w+ заявител\w+", 3),
    ],
    RequestType.RKN_ORDER: [
        (r"предписани\w+ об устранении", 5),
        (r"выдан\w* предписани\w+|направляем предписани\w+", 4.5),
        (r"устранить выявленн\w+ нарушени\w+", 4),
        (r"об исполнении предписани\w+ (?:уведомить|сообщить)", 4),
    ],
    RequestType.RKN_INSPECTION: [
        (r"решени\w+ о проведении .{0,40}(?:проверк|контрольн\w+ (?:надзорн\w+ )?мероприят)\w+", 4.5),
        (r"(?:плановая|внеплановая) (?:документарная |выездная )?проверк\w+", 4),
        (r"контрольн\w+ надзорн\w+ мероприяти\w+|\bкнм\b", 4),
        (r"мотивированн\w+ запрос\w* .{0,40}(?:в рамках|при проведении)", 3),
    ],
    RequestType.RKN_INCIDENT_FOLLOWUP: [
        (r"инцидент\w* .{0,40}персональн\w+ данн\w+", 4),
        (r"неправомерн\w+ (?:передач|раскрыт|доступ)\w+ .{0,30}персональн\w+ данн\w+", 4),
        (r"ч\.?\s?3\.1 ст\.?\s?21|част\w* 3\.1", 5),
        (r"(?:в течение )?(?:24|двадцати четырех) часов", 3),
        (r"(?:в течение )?(?:72|семидесяти двух) часов", 3),
        (r"результат\w+ внутреннего расследовани\w+", 4),
    ],
    RequestType.AUTHORITY_REQUEST: [
        (r"прокуратур\w+|следственн\w+ (?:комитет|отдел)|мвд|увд|гувд|фсб|фнс|налогов\w+ орган", 3.5),
        (r"судебн\w+ (?:запрос|определени|решени)|мировой судья|арбитражн\w+ суд", 3.5),
        (r"судебн\w+ пристав|фссп", 3.5),
    ],

    # --- не про ПД --------------------------------------------------------- #
    RequestType.COOPERATION_OFFER: [
        (r"предложени\w+ о сотрудничеств\w+", 5),
        (r"коммерческо\w+ предложени\w+", 5),
        (r"(?:предлагаем|хотели бы предложить) (?:вам )?(?:сотрудничеств|партнерств|услуг|наш\w+ (?:продукт|решени|платформ|сервис))\w*", 4.5),
        (r"рассмотреть возможность сотрудничеств\w+", 4.5),
        (r"мы (?:являемся )?(?:компани|разработчик|поставщик|интегратор)\w+ .{0,60}(?:предлага|готовы)\w+", 3.5),
        (r"наша компания специализируется", 4),
        (r"готовы обсудить условия (?:сотрудничеств|поставк|партнерств)\w+", 4),
        (r"презентаци\w+ наш\w+ (?:решени|продукт|услуг)\w+", 3.5),
        (r"(?:бесплатн\w+ )?демо[- ]?(?:доступ|версия|показ)", 3),
    ],
    RequestType.CONSUMER_CLAIM: [
        (r"защит\w+ прав потребител\w+|зозпп", 4.5),
        (r"(?:прошу|требую) верн\w+ (?:мне )?(?:деньги|денежн\w+ средств|стоимост|оплат)\w*", 4),
        (r"возврат\w* (?:денежн\w+ средств|товар|стоимост)\w*", 3.5),
        (r"(?:товар|услуг\w+) (?:ненадлежащ\w+ качеств|с недостатк|бракован)\w*", 4),
        (r"расторж\w+ договор\w+ .{0,30}(?:купли[- ]продажи|оказани\w+ услуг)", 3.5),
        (r"(?:мой )?заказ (?:№\s?\d+|номер \d+).{0,60}(?:не (?:пришел|доставлен)|отменит|верните)", 3.5),
        (r"гарантийн\w+ (?:срок|ремонт|случа)\w*", 3),
        (r"неустойк\w+|пен\w+ за просрочк\w+", 3),
    ],
    RequestType.TECH_SUPPORT: [
        (r"не (?:работает|открывается|загружается|приходит смс)", 3),
        (r"не могу (?:войти|зайти|авторизоваться|восстановить пароль)", 3.5),
        (r"ошибк\w+ (?:при|в) (?:вход|оплат|регистрац|приложен)\w+", 3),
        (r"сброс\w+ парол\w+|восстановлени\w+ парол\w+", 3),
        (r"технич\w+ (?:проблем|сбо|поддержк)\w+", 3),
    ],
    RequestType.HR_QUESTION: [
        (r"(?:прошу выдать|выдайте|нужна) справк\w+ (?:2[- ]?ндфл|о доход|с места работы)", 4),
        (r"расчетн\w+ лист\w*|копи\w+ трудов\w+ книжк\w+", 3.5),
        (r"(?:начислен|выплат|задолженност)\w+ (?:по )?(?:заработн\w+ плат|зарплат|отпускн)\w+", 3.5),
        (r"график отпуск\w+|оформлени\w+ отпуск\w+|больничн\w+ лист", 3),
    ],
    RequestType.JOB_APPLICATION: [
        (r"отклик на вакансию|мое резюме|прилагаю резюме|направляю резюме", 4.5),
        (r"рассмотреть мою кандидатур\w+ на (?:должност|ваканс)\w+", 4.5),
        (r"ищу работу|хочу работать у вас", 4),
    ],
    RequestType.BILLING: [
        (r"(?:выставит|направит|пришлит)\w+ счет(?:[- ]фактур\w+)?", 3.5),
        (r"акт сверк\w+|закрывающ\w+ документ\w+|упд\b", 3.5),
        (r"оплат\w+ по договору №", 3),
    ],
    RequestType.SPAM: [
        (r"вы выиграли|получите приз|заработок в интернете|крипто[- ]?(?:инвестиц|трейдинг)", 4),
        (r"unsubscribe|отписаться от рассылки", 1.5),
        (r"продвижени\w+ (?:сайт|бизнес)\w+ в (?:топ|яндекс|google)", 4),
        (r"seo[- ]продвижени|увеличим ваши продажи", 4),
    ],
}


# --------------------------------------------------------------------------- #
#  Правила: кто обращается
# --------------------------------------------------------------------------- #

RKN_EMAIL_DOMAINS = (
    "rkn.gov.ru", "rsoc.ru", "roskomnadzor.ru",
)

REQUESTER_PATTERNS: dict[RequesterKind, list[tuple[str, float]]] = {
    RequesterKind.RKN: [
        (r"роскомнадзор", 5),
        (r"федеральн\w+ служб\w+ по надзору в сфере связи", 5),
        (r"управлени\w+ роскомнадзора по", 5),
        (r"уполномоченн\w+ орган\w* по защите прав субъектов персональных данных", 4.5),
    ],
    RequesterKind.OTHER_AUTHORITY: [
        (r"прокуратур\w+|следственн\w+ комитет|\bмвд\b|\bувд\b|\bфсб\b|\bфнс\b", 4),
        (r"судебн\w+ пристав|\bфссп\b|мировой судья|районн\w+ суд|арбитражн\w+ суд", 4),
        (r"трудов\w+ инспекци\w+|роспотребнадзор|\bгит\b", 4),
    ],
    RequesterKind.SUBJECT_REPRESENTATIVE: [
        (r"действу\w+ (?:от имени|в интересах) .{0,40}на основании доверенности", 5),
        (r"по доверенности от", 4.5),
        (r"я, (?:адвокат|представитель)", 4),
        (r"в интересах моего доверител\w+", 4.5),
        (r"законн\w+ представител\w+ (?:несовершеннолетн|ребенк)\w+", 4.5),
        (r"ордер адвокат\w+", 4),
    ],
    RequesterKind.COMPANY: [
        (r"\bооо\b|\bао\b|\bпао\b|\bзао\b|\bип\b\s|общество с ограниченной ответственностью", 1.5),
        (r"генеральн\w+ директор|коммерческ\w+ директор", 1.5),
    ],
    RequesterKind.SUBJECT: [
        (r"\bя,?\s|\bя\s+(?:прошу|требую|отзыва|являюсь|работа|зарегистр)\w*", 2),
        (r"(?:мои|моих|моими|моим) (?:персональн\w+ )?данн\w+", 2.5),
        (r"(?:прошу|требую) (?:вас )?(?:предоставить|удалить|уточнить|заблокировать|"
         r"уничтожить|прекратить|исправить)", 2),
        (r"\bмо(?:й|я|е|и|его|ей|ему)\b|\bменя\b|\bмне\b", 1),
    ],
}


# --------------------------------------------------------------------------- #
#  Правила: вид субъекта ПД
# --------------------------------------------------------------------------- #

SUBJECT_PATTERNS: dict[SubjectType, list[tuple[str, float]]] = {
    SubjectType.EMPLOYEE: [
        (r"трудов\w+ договор\w*", 4),
        (r"я работа\w+ в (?:вашей )?(?:компани|организац|обществ)\w+", 4.5),
        (r"(?:являюсь|как) (?:вашим )?(?:работник|сотрудник)\w+", 4.5),
        (r"табельн\w+ номер|мое подразделени|отдел кадров", 3),
        (r"заработн\w+ плат\w+|расчетн\w+ листок", 2.5),
        (r"личн\w+ дел\w+ работник\w+", 3.5),
    ],
    SubjectType.FORMER_EMPLOYEE: [
        (r"был\w* уволен\w*|после увольнени\w+|уволил\w+ся", 4.5),
        (r"бывш\w+ (?:работник|сотрудник)\w*", 5),
        (r"трудов\w+ договор .{0,30}расторгнут", 4),
    ],
    SubjectType.CANDIDATE: [
        (r"проходил\w* собеседовани\w+|направлял\w* резюме|мое резюме", 4.5),
        (r"откликал\w+ся на вакансию|кандидат\w* на должност\w+", 4.5),
        (r"отказ\w* в трудоустройств\w+|не прошел отбор", 4),
        (r"базы (?:ваших )?кандидат\w+|кадров\w+ резерв", 3.5),
    ],
    SubjectType.USER: [
        (r"личн\w+ кабинет\w*|учетн\w+ запис\w+|мой аккаунт|мой профил\w+", 4),
        (r"зарегистрирован\w* (?:на|в) (?:ваш\w+ |наш\w+ |мобильн\w+ |сво\w+ )*(?:сайт|приложени|сервис|платформ|портал)\w*", 4.5),
        (r"пользу\w+сь (?:ваш\w+ |наш\w+ |мобильн\w+ )*(?:сервис|приложени|платформ|сайт)\w+", 4.5),
        (r"(?:в|на) (?:ваш\w+ )?(?:мобильн\w+ )?(?:приложени|личн\w+ кабинет|сервис)\w*", 2),
        (r"логин|никнейм|мобильн\w+ приложени\w+", 2),
    ],
    SubjectType.CONSUMER: [
        (r"(?:мой |оформил\w* )?заказ (?:№|номер|n)\s?\w+", 3.5),
        (r"приобрел\w*|купил\w*|оплатил\w* товар", 4),
        (r"договор\w* (?:купли[- ]продажи|оказани\w+ услуг|розничн\w+)", 3.5),
        (r"как потребител\w+|прав\w* потребител\w+", 4.5),
        (r"доставк\w+ (?:товар|заказ)\w+|чек (?:об оплате|№)", 3),
    ],
    SubjectType.COUNTERPARTY_REP: [
        (r"представител\w+ (?:наш\w+ )?контрагент\w+", 5),
        (r"(?:договор|контракт) (?:поставк|подряд|оказани\w+ услуг|аренд)\w+", 3.5),
        (r"(?:мои|наши) данн\w+ (?:были )?передан\w+ (?:вам )?(?:нашим|моим) работодател\w+", 4.5),
        (r"подписант\w*|уполномоченн\w+ лиц\w+ по договору", 3.5),
        (r"мои данные указаны в договоре", 4),
    ],
}


# --------------------------------------------------------------------------- #
#  Извлечение реквизитов
# --------------------------------------------------------------------------- #

RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
RE_PHONE = re.compile(r"(?:\+7|8)[\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")
RE_INN = re.compile(r"\bинн[\s:№]*(\d{10}|\d{12})\b")
RE_PASSPORT = re.compile(r"паспорт\w*[\s:]*(?:серия\s*)?(\d{2}\s?\d{2})\s*(?:№|номер|n)?\s*(\d{6})")
RE_CONTRACT = re.compile(r"договор\w*\s*(?:№|n|номер)\s*([\w/-]{1,25})")
RE_ORDER = re.compile(r"заказ\w*\s*(?:№|n|номер)\s*([\w/-]{1,25})")
RE_OUTGOING = re.compile(r"(?:исх\.?|исходящий)\s*(?:№|n)\s*([\w/.-]{1,30})")
RE_DEADLINE_IN_DOC = re.compile(
    r"в срок (?:до|не позднее)\s+(\d{1,2}[.\s]\d{1,2}[.\s]\d{2,4})"
)
RE_DEADLINE_DAYS = re.compile(
    r"в течение (\d{1,3}|дес\w+|пят\w+|семи|трех|тридцати)\s*(рабочих|календарных)?\s*(?:дн|суто)\w*"
)
RE_ENTITY = re.compile(
    r"(?:ооо|оао|зао|пао|ао|нао)\s+[«\"']([^»\"'\n]{2,120})[»\"']"
    r"|(?:общество с ограниченной ответственностью)\s+[«\"']([^»\"'\n]{2,120})[»\"']",
    re.IGNORECASE,
)


def extract_details(raw_text: str) -> dict:
    """Вытащить реквизиты из исходного (ненормализованного) текста."""
    t = raw_text or ""
    low = normalize(t)
    out: dict = {}

    emails = [e for e in RE_EMAIL.findall(t)]
    if emails:
        out["emails"] = sorted(set(emails))[:10]
    phones = RE_PHONE.findall(t)
    if phones:
        out["phones"] = sorted(set(p.strip() for p in phones))[:5]
    if m := RE_INN.search(low):
        out["inn"] = m.group(1)
    if m := RE_PASSPORT.search(low):
        # Сам номер не сохраняем — минимизация ПД (ст. 5 ч. 5 ФЗ-152),
        # фиксируем только факт наличия реквизита для проверки по ч. 4 ст. 14.
        out["passport_present"] = True
    if m := RE_CONTRACT.search(low):
        out["contract_number"] = m.group(1)
    if m := RE_ORDER.search(low):
        out["order_number"] = m.group(1)
    if m := RE_OUTGOING.search(low):
        out["outgoing_number"] = m.group(1)
    if m := RE_DEADLINE_IN_DOC.search(low):
        out["deadline_in_document"] = m.group(1)
    if m := RE_DEADLINE_DAYS.search(low):
        out["deadline_days_phrase"] = m.group(0)
    ent = RE_ENTITY.search(t)
    if ent:
        out["legal_entity_quoted"] = (ent.group(1) or ent.group(2) or "").strip()
    return out


# --------------------------------------------------------------------------- #
#  Скоринг
# --------------------------------------------------------------------------- #

def _score(text_norm: str, subject_norm: str, patterns: dict, kind: str
           ) -> tuple[dict, list[Signal]]:
    """Тема письма весит в 1.5 раза больше тела — там обычно суть требования."""
    scores: dict = {}
    signals: list[Signal] = []
    for key, rules in patterns.items():
        total = 0.0
        for pattern, weight in rules:
            rx = re.compile(pattern)
            if m := rx.search(subject_norm):
                total += weight * 1.5
                signals.append(Signal(kind, key.value, weight * 1.5, m.group(0), "subject"))
            elif m := rx.search(text_norm):
                total += weight
                signals.append(Signal(kind, key.value, weight, m.group(0), "body"))
        if total:
            scores[key] = total
    return scores, signals


def _pick(scores: dict, default):
    if not scores:
        return default, 0.0, 0.0
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ordered[0]
    runner = ordered[1][1] if len(ordered) > 1 else 0.0
    return best, best_score, runner


def _confidence(best: float, runner: float) -> float:
    """Уверенность растёт с абсолютным весом и с отрывом от второго места."""
    if best <= 0:
        return 0.0
    saturation = min(best / 9.0, 1.0)
    margin = (best - runner) / best
    return round(min(0.99, 0.45 * saturation + 0.55 * (0.35 + 0.65 * margin) * saturation), 3)


def classify(
    *,
    body: str,
    subject_line: str = "",
    from_email: str = "",
    inbox_purpose: str = "",
    attachments_text: str = "",
) -> Classification:
    """Основная точка входа классификатора."""
    raw = "\n".join(x for x in (subject_line, body, attachments_text) if x)
    text_norm = normalize("\n".join(x for x in (body, attachments_text) if x))
    subj_norm = normalize(subject_line)
    from_email = (from_email or "").lower().strip()

    res = Classification()
    res.extracted = extract_details(raw)

    # --- кто обращается ---------------------------------------------------- #
    rq_scores, rq_signals = _score(text_norm, subj_norm, REQUESTER_PATTERNS, "REQUESTER")
    if any(from_email.endswith("@" + d) or from_email.endswith("." + d)
           for d in RKN_EMAIL_DOMAINS):
        rq_scores[RequesterKind.RKN] = rq_scores.get(RequesterKind.RKN, 0) + 6
        rq_signals.append(Signal("REQUESTER", RequesterKind.RKN.value, 6, from_email, "from"))
    rq_best, rq_s, rq_r = _pick(rq_scores, RequesterKind.UNKNOWN)
    res.requester_kind = rq_best
    res.signals.extend(rq_signals)

    # --- тип обращения ----------------------------------------------------- #
    t_scores, t_signals = _score(text_norm, subj_norm, TYPE_PATTERNS, "TYPE")

    # Роскомнадзор не может подать «запрос субъекта» — гасим неподходящие ветки.
    if res.requester_kind is RequesterKind.RKN:
        for t in list(t_scores):
            if t not in RKN_TYPES:
                t_scores[t] *= 0.25
        for t in RKN_TYPES:
            t_scores.setdefault(t, 0.0)
        if max((t_scores.get(t, 0) for t in RKN_TYPES), default=0) < 2:
            # Письмо от РКН без явных маркеров — по умолчанию запрос информации.
            t_scores[RequestType.RKN_INFO_REQUEST] = t_scores.get(
                RequestType.RKN_INFO_REQUEST, 0) + 3
            t_signals.append(Signal("TYPE", RequestType.RKN_INFO_REQUEST.value, 3,
                                    "отправитель — Роскомнадзор", "from"))
    elif res.requester_kind is RequesterKind.OTHER_AUTHORITY:
        t_scores[RequestType.AUTHORITY_REQUEST] = t_scores.get(
            RequestType.AUTHORITY_REQUEST, 0) + 3
    else:
        # Не-РКН отправитель не может прислать предписание/уведомление о проверке.
        for t in RKN_TYPES:
            if t in t_scores:
                t_scores[t] *= 0.3

    # Тематика ящика как слабый приор.
    purpose = (inbox_purpose or "").lower()
    if purpose in ("privacy", "dpo"):
        for t in (RequestType.ACCESS, RequestType.ERASURE, RequestType.CONSENT_WITHDRAWAL,
                  RequestType.RECTIFICATION, RequestType.UNLAWFUL_PROCESSING):
            if t in t_scores:
                t_scores[t] += 0.8
    elif purpose == "hr":
        for t in (RequestType.HR_QUESTION, RequestType.JOB_APPLICATION):
            if t in t_scores:
                t_scores[t] += 0.8
    elif purpose == "support":
        for t in (RequestType.TECH_SUPPORT, RequestType.CONSUMER_CLAIM):
            if t in t_scores:
                t_scores[t] += 0.8

    t_scores = {k: v for k, v in t_scores.items() if v > 0}
    t_best, t_s, t_r = _pick(t_scores, RequestType.UNCLASSIFIED)
    res.request_type = t_best
    res.confidence = _confidence(t_s, t_r)
    res.scores = {k.value: v for k, v in t_scores.items()}
    res.signals.extend(t_signals)

    # Дополнительные требования в том же письме (составное обращение).
    res.secondary_types = [
        t for t, s in sorted(t_scores.items(), key=lambda kv: -kv[1])
        if t is not t_best and s >= 3.0 and t in _COMBINABLE
    ][:3]

    # Требование по ФЗ-152 без признаков органа/представителя подаёт сам субъект.
    if res.requester_kind is RequesterKind.UNKNOWN or (
        res.requester_kind is RequesterKind.COMPANY and rq_s < 3.0
    ):
        from .domain import SUBJECT_TYPES_OF_REQUEST
        if res.request_type in SUBJECT_TYPES_OF_REQUEST and res.confidence >= 0.4:
            res.requester_kind = RequesterKind.SUBJECT
            res.signals.append(Signal(
                "REQUESTER", RequesterKind.SUBJECT.value, 1.0,
                "требование по ФЗ-152 без признаков органа или представителя", "inferred"))

    # --- вид субъекта ------------------------------------------------------ #
    s_scores, s_signals = _score(text_norm, subj_norm, SUBJECT_PATTERNS, "SUBJECT")
    s_best, s_s, s_r = _pick(s_scores, SubjectType.UNKNOWN)
    if s_s >= 3.0:
        res.subject_type = s_best
    res.signals.extend(s_signals)

    if res.requester_kind is RequesterKind.RKN:
        # У письма РКН вид субъекта берётся из пересланной жалобы, если он там есть.
        if s_s < 3.0:
            res.subject_type = SubjectType.UNKNOWN

    if res.request_type is RequestType.JOB_APPLICATION and res.subject_type is SubjectType.UNKNOWN:
        res.subject_type = SubjectType.CANDIDATE
    if res.request_type is RequestType.CONSUMER_CLAIM and res.subject_type is SubjectType.UNKNOWN:
        res.subject_type = SubjectType.CONSUMER
    if res.request_type is RequestType.HR_QUESTION and res.subject_type is SubjectType.UNKNOWN:
        res.subject_type = SubjectType.EMPLOYEE

    res.legal_entity_mentioned = res.extracted.get("legal_entity_quoted", "")
    res.flags = propose_flags(res, text_norm, subj_norm, from_email)
    res.summary = build_summary(res)
    return res


#: Типы, которые осмысленно сочетаются в одном письме.
_COMBINABLE: frozenset[RequestType] = frozenset({
    RequestType.ACCESS, RequestType.ERASURE, RequestType.CONSENT_WITHDRAWAL,
    RequestType.RECTIFICATION, RequestType.BLOCKING, RequestType.STOP_MARKETING,
    RequestType.UNLAWFUL_PROCESSING, RequestType.CROSS_BORDER_INFO,
    RequestType.CONFIRM_PROCESSING, RequestType.CONSUMER_CLAIM,
})


# --------------------------------------------------------------------------- #
#  Флажки
# --------------------------------------------------------------------------- #

def propose_flags(res: Classification, text_norm: str, subj_norm: str,
                  from_email: str) -> list[FlagProposal]:
    """
    Красный флажок — обращение очевидно не про персональные данные.
    Синий флажок — спорный момент, который должен решить DPO.
    """
    flags: list[FlagProposal] = []
    full = f"{subj_norm} {text_norm}"
    pd_mentioned = bool(re.search(r"персональн\w+ данн\w+|\bпдн\b|152[- ]?фз|фз[- ]?152", full))

    # ---------------- красные ---------------------------------------------- #
    if res.request_type in NON_PD_TYPES:
        if res.confidence >= 0.5 and not pd_mentioned:
            flags.append(FlagProposal(
                Flag.RED, f"NON_PD_{res.request_type.value}",
                f"Классифицировано как «{res.request_type.value}» — вне периметра ФЗ-152. "
                f"Персональные данные в тексте не упоминаются. "
                f"Рекомендуется передать профильной команде.",
            ))
        elif pd_mentioned:
            flags.append(FlagProposal(
                Flag.BLUE, "MIXED_PD_AND_NON_PD",
                "Обращение выглядит как непрофильное, но в тексте упоминаются персональные "
                "данные / ФЗ-152. Возможно составное обращение: часть требований подпадает "
                "под ФЗ-152 и имеет собственный срок. Проверьте вручную.",
            ))

    if res.request_type is RequestType.UNCLASSIFIED and not pd_mentioned:
        flags.append(FlagProposal(
            Flag.RED, "NO_PD_SIGNALS",
            "Не найдено ни одного признака требования по ФЗ-152 и нет упоминания "
            "персональных данных. Вероятно, обращение не относится к компетенции DPO.",
        ))

    # ---------------- синие ------------------------------------------------ #
    if res.request_type is RequestType.UNCLASSIFIED and pd_mentioned:
        flags.append(FlagProposal(
            Flag.BLUE, "PD_MENTIONED_BUT_UNCLEAR",
            "Персональные данные упоминаются, но конкретное требование не распознано. "
            "Нужна ручная квалификация: от типа зависит срок (10 / 7 / 30 дней).",
        ))

    if 0 < res.confidence < 0.45 and res.request_type is not RequestType.UNCLASSIFIED:
        flags.append(FlagProposal(
            Flag.BLUE, "LOW_CONFIDENCE",
            f"Низкая уверенность классификации ({res.confidence:.0%}). "
            f"Проверьте тип обращения — от него зависит расчёт срока.",
        ))

    top = sorted(res.scores.items(), key=lambda kv: -kv[1])[:2]
    if len(top) == 2 and top[1][1] >= top[0][1] * 0.8:
        flags.append(FlagProposal(
            Flag.BLUE, "AMBIGUOUS_TYPE",
            f"Два типа набрали близкий вес: «{top[0][0]}» и «{top[1][0]}». "
            f"Возможно составное обращение — тогда срок считается по наиболее строгому.",
        ))

    if res.secondary_types:
        flags.append(FlagProposal(
            Flag.BLUE, "COMPOSITE_REQUEST",
            "В одном письме несколько требований: "
            + ", ".join(t.value for t in res.secondary_types)
            + ". Каждое имеет свой срок — ответ должен закрывать все.",
        ))

    # Представитель без подтверждения полномочий (ч. 4 ст. 14 + ч. 3 ст. 14).
    if res.requester_kind is RequesterKind.SUBJECT_REPRESENTATIVE:
        if not re.search(r"доверенност\w+|ордер|свидетельств\w+ о рождении|опекун", full):
            flags.append(FlagProposal(
                Flag.BLUE, "REPRESENTATIVE_NO_POA",
                "Обращается представитель, но документ о полномочиях не упомянут. "
                "До подтверждения полномочий сведения предоставлять нельзя — "
                "запросите доверенность и зафиксируйте дату подтверждения.",
            ))
        else:
            flags.append(FlagProposal(
                Flag.BLUE, "REPRESENTATIVE_CHECK_POA",
                "Обращение от представителя. Проверьте объём полномочий по доверенности: "
                "право на получение персональных данных доверителя должно быть прямо указано.",
            ))

    # Идентификация по ч. 4 ст. 14.
    if res.request_type in _NEEDS_IDENTITY:
        has_id = bool(res.extracted.get("passport_present")) or bool(
            re.search(r"паспорт|удостоверя\w+ личност\w+|снилс|номер договора|"
                      r"номер заказа|логин|учетн\w+ запис\w+", full))
        if not has_id:
            flags.append(FlagProposal(
                Flag.BLUE, "IDENTITY_NOT_PROVEN",
                "В запросе не приведены сведения по ч. 4 ст. 14 ФЗ-152 (реквизиты документа, "
                "удостоверяющего личность, и сведения, подтверждающие участие в отношениях "
                "с оператором). Формально 10-дневный срок начинает течь с даты получения "
                "надлежащего запроса — зафиксируйте дату подтверждения личности.",
            ))

    # Отзыв согласия при вероятном ином основании обработки.
    if res.request_type is RequestType.CONSENT_WITHDRAWAL:
        flags.append(FlagProposal(
            Flag.BLUE, "WITHDRAWAL_OTHER_BASIS",
            "Отзыв согласия не прекращает обработку, которая ведётся на иных основаниях "
            "ч. 1 ст. 6 ФЗ-152 (исполнение договора, требования ТК РФ, ФЗ-402, ФЗ-115, "
            "судебный акт). Проверьте основания по каждому сервису и явно перечислите "
            "в ответе, что продолжает обрабатываться и почему.",
        ))

    # Уничтожение при действующих сроках хранения.
    if res.request_type is RequestType.ERASURE:
        flags.append(FlagProposal(
            Flag.BLUE, "ERASURE_RETENTION_CONFLICT",
            "Требование об уничтожении конфликтует с обязательными сроками хранения "
            "(кадровые документы — Приказ Росархива № 236, бухгалтерия — ФЗ-402, "
            "ФЗ-115). Уничтожить можно только то, что не подпадает под эти сроки; "
            "остальное — блокировать и уведомить субъекта об основаниях.",
        ))

    # Данные третьего лица.
    if re.search(r"данн\w+ (?:моего|моей|мо[ei]й) (?:супруг|ребенк|сына|дочер|родственник|"
                 r"коллег|сотрудник)\w*|персональн\w+ данн\w+ (?:третьего лица|другого человека)",
                 full):
        flags.append(FlagProposal(
            Flag.BLUE, "THIRD_PARTY_DATA",
            "Запрашиваются сведения о другом субъекте ПД. Предоставление возможно только "
            "при подтверждённых полномочиях, иначе — мотивированный отказ "
            "(ч. 8 ст. 14 ФЗ-152: права третьих лиц).",
        ))

    # Спор о том, кто оператор.
    if re.search(r"я не (?:являюсь|был\w*) (?:вашим )?(?:клиент|пользовател|работник)\w*|"
                 r"никогда не (?:регистрировал|обращал)\w*|не заключал\w* (?:с вами )?договор",
                 full):
        flags.append(FlagProposal(
            Flag.BLUE, "SUBJECT_RELATION_DISPUTED",
            "Заявитель отрицает наличие отношений с оператором. Если данных нет — надо дать "
            "ответ об отсутствии обработки; если есть — выяснить источник получения "
            "(п. 4 ч. 7 ст. 14) и правомерность сбора.",
        ))

    if res.subject_type is SubjectType.UNKNOWN and res.request_type not in NON_PD_TYPES \
            and res.request_type is not RequestType.UNCLASSIFIED \
            and res.requester_kind is not RequesterKind.RKN:
        flags.append(FlagProposal(
            Flag.BLUE, "SUBJECT_TYPE_UNKNOWN",
            "Не удалось определить вид субъекта ПД (работник / пользователь / кандидат / "
            "потребитель / представитель контрагента). От этого зависит, в каких системах "
            "искать данные и какие сроки хранения применяются.",
        ))

    if res.requester_kind in (RequesterKind.RKN, RequesterKind.OTHER_AUTHORITY):
        found = res.extracted.get("deadline_in_document")
        if found:
            flags.append(FlagProposal(
                Flag.BLUE, "AUTHORITY_DEADLINE_FOUND",
                f"В документе указан срок: {found}. Он имеет приоритет над расчётным — "
                f"перенесите его в поле «Срок из документа», иначе реестр будет "
                f"показывать срок по умолчанию.",
            ))
        elif not res.extracted.get("deadline_days_phrase"):
            flags.append(FlagProposal(
                Flag.BLUE, "AUTHORITY_DEADLINE_NOT_FOUND",
                "В письме органа не найден явный срок ответа. Срок из документа имеет "
                "приоритет над расчётным — перечитайте документ и задайте срок вручную.",
            ))

    if res.request_type in RKN_TYPES and res.requester_kind is not RequesterKind.RKN:
        flags.append(FlagProposal(
            Flag.BLUE, "RKN_TYPE_WRONG_SENDER",
            "Текст похож на документ Роскомнадзора, но адрес отправителя не относится "
            "к домену ведомства. Проверьте подлинность — возможна фишинговая рассылка.",
        ))

    return flags


#: Типы, для которых по ч. 4 ст. 14 нужна идентификация заявителя.
_NEEDS_IDENTITY: frozenset[RequestType] = frozenset({
    RequestType.ACCESS, RequestType.CONFIRM_PROCESSING, RequestType.RECTIFICATION,
    RequestType.BLOCKING, RequestType.ERASURE, RequestType.CROSS_BORDER_INFO,
    RequestType.AUTOMATED_DECISION,
})


def build_summary(res: Classification) -> str:
    from .domain import REQUEST_TYPE_LABELS, SUBJECT_TYPE_LABELS
    parts = [REQUEST_TYPE_LABELS.get(res.request_type, res.request_type.value)]
    if res.requester_kind is RequesterKind.RKN:
        parts.append("отправитель — Роскомнадзор")
    elif res.requester_kind is RequesterKind.SUBJECT_REPRESENTATIVE:
        parts.append("обращается представитель субъекта")
    if res.subject_type is not SubjectType.UNKNOWN:
        parts.append(SUBJECT_TYPE_LABELS[res.subject_type].lower())
    if res.secondary_types:
        parts.append(f"дополнительно: {len(res.secondary_types)} требовани(е/я)")
    return "; ".join(parts) + "."


def _contains_word(haystack: str, needle: str) -> bool:
    """
    Вхождение с границами слов и допуском русского словоизменения в хвосте.

    Без этого «приложением документов» ошибочно матчится на ключевое слово
    «приложение», а «Ромашкина» — на «Ромашка».
    """
    needle = normalize(needle).strip()
    if len(needle) < 3:
        return False
    # Хвост до 3 букв допускаем: «заказ» → «заказа», «заказом».
    pattern = r"(?<![\w-])" + re.escape(needle) + r"(?:[а-яё]{0,3})?(?![\w-])"
    return re.search(pattern, haystack) is not None


def match_legal_entity(text: str, entities: list) -> tuple[int | None, str]:
    """Сопоставить ЮЛ из справочника с текстом обращения."""
    low = normalize(text)
    best: tuple[int | None, str, int] = (None, "", 0)
    for e in entities:
        candidates = [e.name, e.short_name, *(e.aliases or [])]
        if e.inn:
            candidates.append(e.inn)
        for c in candidates:
            c = (c or "").strip()
            if len(c) < 3:
                continue
            if _contains_word(low, c) and len(c) > best[2]:
                best = (e.id, e.name, len(c))
    return best[0], best[1]


def _stem_pattern(token: str) -> str:
    """
    Шаблон для одного слова с допуском на русское словоизменение.

    Основа обрезается максимум на 3 символа и не короче 5, иначе «заказ»
    выродится в «зак» и начнёт совпадать с «законом». Обязательная граница
    справа не даёт «договор» поймать «договорённость».
    """
    token = token.strip()
    if len(token) < 5:
        return re.escape(token) + r"[а-яё]{0,2}"
    stem = token[: max(5, len(token) - 3)]
    return re.escape(stem) + r"[а-яё]{0,4}"


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """
    Вхождение фразы с учётом склонения каждого слова.

    «мобильное приложение» находится в «зарегистрирован в мобильном приложении»,
    но «приложение» в одиночку не притянет «с приложением документов», потому что
    там нет первого слова фразы.
    """
    phrase = normalize(phrase).strip()
    if len(phrase) < 3:
        return False
    tokens = [t for t in re.split(r"[^\w]+", phrase) if t]
    if not tokens:
        return False
    pattern = r"(?<![\w-])" + r"[^\w]+".join(_stem_pattern(t) for t in tokens) + r"(?![\w-])"
    return re.search(pattern, haystack) is not None


#: Минимальный вес, при котором привязка к сервису считается достоверной.
MIN_SERVICE_SCORE = 3.0


def match_service(text: str, services: list, subject_type: SubjectType | None = None
                  ) -> tuple[int | None, str]:
    """Сопоставить сервис / бизнес-процесс с текстом обращения."""
    low = normalize(text)
    scored: list[tuple[float, int, str]] = []
    for s in services:
        score = 0.0
        for kw in (s.keywords or []):
            kw = (kw or "").strip()
            if _contains_phrase(low, kw):
                score += 2 + len(kw) / 40
        if s.name and len(s.name) >= 4 and _contains_phrase(low, s.name):
            score += 3
        if s.code and len(s.code) >= 3 and _contains_word(low, s.code):
            score += 2
        if score and subject_type and subject_type.value in (s.subject_types or []):
            score += 1.5
        if score:
            scored.append((score, s.id, s.name))
    # Порог отсекает совпадение по одному короткому общему слову: «приложение»
    # встречается и в «мобильном приложении», и в «с приложением документов».
    scored = [row for row in scored if row[0] >= MIN_SERVICE_SCORE]
    if not scored:
        return None, ""
    scored.sort(reverse=True)
    return scored[0][1], scored[0][2]
