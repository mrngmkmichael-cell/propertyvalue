"""Interface languages.

What is translated: the site's own words. Navigation, the search form,
the pitch, the explanation of what this site is and how to read it.

What is NOT translated, deliberately: the data. School names, street
names, local authorities and the names of the bodies that publish each
figure ("HM Land Registry", "Ofsted") are proper nouns, and a
translated proper noun cannot be checked against its source, which is
the one thing this site promises. The figures themselves are numbers
and need no translation. Every translated page says this in the
reader's own language rather than letting them discover it.

Why interface-only rather than the whole site: there are 2,943 area
guides and 616 school pages. Translated eight ways that is over 28,000
machine-translated URLs, submitted by a domain currently getting 21
pages indexed. Google reads that as thin duplicate content at scale and
it would cost more than it earned. The translated landing pages here
are hand-written, few, and worth indexing.

Fonts: the site's own faces are Latin-only (see app/static/fonts/), so
each script names a stack of faces that ship with the major operating
systems. Self-hosting Noto for Han, Kana, Hangul, Arabic and Devanagari
would add well over 10 MB to a site whose whole point is being quick.
"""

# Latin faces already self-hosted; the site's own stack applies.
_LATIN = "'Instrument Sans', system-ui, -apple-system, 'Segoe UI', sans-serif"
_SC = ("'Instrument Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', "
       "'Source Han Sans SC', 'Noto Sans CJK SC', sans-serif")
_TC = ("'Instrument Sans', 'PingFang TC', 'Hiragino Sans CNS', 'Microsoft JhengHei', "
       "'Source Han Sans TC', 'Noto Sans CJK TC', sans-serif")
_JA = ("'Instrument Sans', 'Hiragino Kaku Gothic ProN', 'Hiragino Sans', 'Yu Gothic', "
       "'Meiryo', 'Noto Sans CJK JP', sans-serif")
_KO = ("'Instrument Sans', 'Apple SD Gothic Neo', 'Malgun Gothic', 'Noto Sans KR', "
       "'Noto Sans CJK KR', sans-serif")
_AR = ("'SF Arabic', 'Geeza Pro', 'Segoe UI', Tahoma, 'Noto Naskh Arabic', "
       "'Noto Sans Arabic', sans-serif")
_HI = ("'Instrument Sans', 'Kohinoor Devanagari', 'Nirmala UI', 'Noto Sans Devanagari', "
       "sans-serif")

# code -> metadata. "code" is what appears in the URL and the cookie;
# "flag" is a country code, and a country is not a language: Arabic
# spans 22 of them and most Spanish speakers are nowhere near Spain.
# The flag is decorative and marked as such; the endonym next to it
# is what actually identifies the row. See app/static/flags/README.md.
# "bcp47" is what goes in lang= and hreflang, where the script subtag
# matters and a bare "zh" would be wrong for either Chinese.
LANGUAGES = {
    "en":      {"endonym": "English",    "english": "English",              "bcp47": "en-GB",   "flag": "gb", "dir": "ltr", "font": _LATIN},
    "zh-hant": {"endonym": "繁體中文",     "english": "Traditional Chinese",  "bcp47": "zh-Hant", "flag": "hk", "dir": "ltr", "font": _TC},
    "zh-hans": {"endonym": "简体中文",     "english": "Simplified Chinese",   "bcp47": "zh-Hans", "flag": "cn", "dir": "ltr", "font": _SC},
    "hi":      {"endonym": "हिन्दी",        "english": "Hindi",                "bcp47": "hi",      "flag": "in", "dir": "ltr", "font": _HI},
    "es":      {"endonym": "Español",     "english": "Spanish",              "bcp47": "es",      "flag": "es", "dir": "ltr", "font": _LATIN},
    "ar":      {"endonym": "العربية",       "english": "Standard Arabic",      "bcp47": "ar",      "flag": "sa", "dir": "rtl", "font": _AR},
    "fr":      {"endonym": "Français",    "english": "French",               "bcp47": "fr",      "flag": "fr", "dir": "ltr", "font": _LATIN},
    "ja":      {"endonym": "日本語",       "english": "Japanese",             "bcp47": "ja",      "flag": "jp", "dir": "ltr", "font": _JA},
    "ko":      {"endonym": "한국어",       "english": "Korean",               "bcp47": "ko",      "flag": "kr", "dir": "ltr", "font": _KO},
}

DEFAULT_LANG = "en"
LANG_COOKIE = "uki_lang"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

# Every string the chrome and the translated landing page need. English
# is the source text and the fallback: a missing key renders in English
# rather than as a blank or a key name, so a half-finished language is
# still a usable page.
STRINGS = {
    # ---- navigation and chrome ----
    "nav.why":        {"en": "Why trust this",  "zh-hant": "為何可信", "zh-hans": "为何可信", "hi": "क्यों भरोसा करें", "es": "Por qué fiarse", "ar": "لماذا تثق بنا", "fr": "Pourquoi nous croire", "ja": "信頼できる理由", "ko": "신뢰할 수 있는 이유"},
    "nav.areas":      {"en": "Area guides",     "zh-hant": "地區指南", "zh-hans": "地区指南", "hi": "क्षेत्र गाइड", "es": "Guías de zona", "ar": "أدلة المناطق", "fr": "Guides de quartier", "ja": "エリアガイド", "ko": "지역 가이드"},
    "nav.schools":    {"en": "School guide",    "zh-hant": "學校指南", "zh-hans": "学校指南", "hi": "स्कूल गाइड", "es": "Guía de colegios", "ar": "دليل المدارس", "fr": "Guide des écoles", "ja": "学校ガイド", "ko": "학교 가이드"},
    "nav.buying":     {"en": "Buying guide",    "zh-hant": "置業指南", "zh-hans": "置业指南", "hi": "खरीद गाइड", "es": "Guía de compra", "ar": "دليل الشراء", "fr": "Guide d'achat", "ja": "購入ガイド", "ko": "구매 가이드"},
    "nav.extension":  {"en": "Extension",       "zh-hant": "瀏覽器擴充功能", "zh-hans": "浏览器扩展", "hi": "एक्सटेंशन", "es": "Extensión", "ar": "الإضافة", "fr": "Extension", "ja": "拡張機能", "ko": "확장 프로그램"},
    "nav.pricing":    {"en": "Pricing",         "zh-hant": "收費", "zh-hans": "收费", "hi": "मूल्य", "es": "Precios", "ar": "الأسعار", "fr": "Tarifs", "ja": "料金", "ko": "요금"},
    "nav.properties": {"en": "My properties",   "zh-hant": "我的物業", "zh-hans": "我的房产", "hi": "मेरी प्रॉपर्टी", "es": "Mis propiedades", "ar": "عقاراتي", "fr": "Mes biens", "ja": "マイ物件", "ko": "내 매물"},
    "nav.login":      {"en": "Log in",          "zh-hant": "登入", "zh-hans": "登录", "hi": "लॉग इन", "es": "Iniciar sesión", "ar": "تسجيل الدخول", "fr": "Connexion", "ja": "ログイン", "ko": "로그인"},
    "nav.logout":     {"en": "Log out",         "zh-hant": "登出", "zh-hans": "退出", "hi": "लॉग आउट", "es": "Cerrar sesión", "ar": "تسجيل الخروج", "fr": "Déconnexion", "ja": "ログアウト", "ko": "로그아웃"},
    "nav.signup":     {"en": "Sign up",         "zh-hant": "註冊", "zh-hans": "注册", "hi": "साइन अप", "es": "Crear cuenta", "ar": "إنشاء حساب", "fr": "Créer un compte", "ja": "新規登録", "ko": "회원가입"},

    # ---- the search form ----
    "form.postcode":  {"en": "UK postcode",     "zh-hant": "英國郵區編號", "zh-hans": "英国邮政编码", "hi": "यूके पोस्टकोड", "es": "Código postal británico", "ar": "الرمز البريدي البريطاني", "fr": "Code postal britannique", "ja": "英国の郵便番号", "ko": "영국 우편번호"},
    "form.house":     {"en": "House or flat number (optional)", "zh-hant": "門牌或單位號碼（可選）", "zh-hans": "门牌或单元号（可选）", "hi": "मकान या फ्लैट नंबर (वैकल्पिक)", "es": "Número de casa o piso (opcional)", "ar": "رقم المنزل أو الشقة (اختياري)", "fr": "Numéro de maison ou d'appartement (facultatif)", "ja": "番地・部屋番号（任意）", "ko": "집 또는 호수 (선택)"},
    "form.search":    {"en": "Search",          "zh-hant": "搜尋", "zh-hans": "搜索", "hi": "खोजें", "es": "Buscar", "ar": "بحث", "fr": "Rechercher", "ja": "検索", "ko": "검색"},

    # ---- the pitch ----
    "hero.title":     {"en": "Any UK postcode. Know before you commit.",
                       "zh-hant": "任何英國郵區。簽約之前，先看清楚。",
                       "zh-hans": "任何英国邮区。签约之前，先看清楚。",
                       "hi": "कोई भी यूके पोस्टकोड। प्रतिबद्ध होने से पहले जान लें।",
                       "es": "Cualquier código postal del Reino Unido. Infórmese antes de comprometerse.",
                       "ar": "أي رمز بريدي في المملكة المتحدة. اعرف قبل أن تلتزم.",
                       "fr": "N'importe quel code postal britannique. Sachez avant de vous engager.",
                       "ja": "英国のどの郵便番号でも。決める前に、確かめる。",
                       "ko": "영국의 모든 우편번호. 계약하기 전에 확인하세요."},
    "hero.dek":       {"en": "Forty checks on any UK address, each one traced back to the government body that published it. Every UK postcode, free, and no account needed.",
                       "zh-hant": "任何英國地址四十項查核，每項數據都註明發布它的政府部門。覆蓋全英郵區，免費使用，無需開立帳戶。",
                       "zh-hans": "任何英国地址四十项查核，每项数据都注明发布它的政府部门。覆盖全英邮区，免费使用，无需开立账户。",
                       "hi": "किसी भी यूके पते पर चालीस जाँच, हर आँकड़े के साथ उसे प्रकाशित करने वाली सरकारी संस्था का नाम। हर यूके पोस्टकोड, मुफ़्त, बिना खाते के।",
                       "es": "Cuarenta comprobaciones sobre cualquier dirección del Reino Unido, cada una con el organismo público que publicó el dato. Todos los códigos postales, gratis y sin cuenta.",
                       "ar": "أربعون فحصًا لأي عنوان في المملكة المتحدة، وكل رقم منسوب إلى الجهة الحكومية التي نشرته. جميع الرموز البريدية، مجانًا وبدون حساب.",
                       "fr": "Quarante vérifications sur n'importe quelle adresse britannique, chacune attribuée à l'organisme public qui l'a publiée. Tous les codes postaux, gratuitement et sans compte.",
                       "ja": "英国のあらゆる住所に対する40項目の調査。すべての数値に、それを公表した政府機関名を明記しています。全郵便番号対応、無料、アカウント不要。",
                       "ko": "영국의 모든 주소에 대한 40가지 확인 항목. 모든 수치에 이를 공개한 정부 기관을 명시합니다. 전 우편번호, 무료, 계정 불필요."},

    # ---- the honest caveat, in the reader's own language ----
    "data.english.title": {"en": "The reports themselves are in English",
                           "zh-hant": "報告內容為英文",
                           "zh-hans": "报告内容为英文",
                           "hi": "रिपोर्ट स्वयं अंग्रेज़ी में हैं",
                           "es": "Los informes están en inglés",
                           "ar": "التقارير نفسها بالإنجليزية",
                           "fr": "Les rapports eux-mêmes sont en anglais",
                           "ja": "レポート本体は英語です",
                           "ko": "리포트 자체는 영어로 제공됩니다"},
    "data.english.body":  {"en": "This page is translated, but a report's contents are not. The figures come from UK government records, and the names of places, schools and the bodies that publish each figure are left exactly as those records write them, so you can check any number against its source. The numbers themselves read the same in any language.",
                           "zh-hant": "本頁為譯文，但報告內容並未翻譯。所有數據來自英國政府紀錄，地名、學校名稱及發布數據的機構名稱一律保留原文，方便你逐項核對來源。數字本身在任何語言中都一樣。",
                           "zh-hans": "本页为译文，但报告内容并未翻译。所有数据来自英国政府记录，地名、学校名称及发布数据的机构名称一律保留原文，方便你逐项核对来源。数字本身在任何语言中都一样。",
                           "hi": "यह पृष्ठ अनूदित है, लेकिन रिपोर्ट की सामग्री नहीं। आँकड़े यूके सरकार के अभिलेखों से आते हैं, और स्थानों, स्कूलों तथा आँकड़े प्रकाशित करने वाली संस्थाओं के नाम वैसे ही रखे गए हैं जैसे उन अभिलेखों में हैं, ताकि आप हर संख्या को उसके स्रोत से मिला सकें। संख्याएँ हर भाषा में एक जैसी पढ़ी जाती हैं।",
                           "es": "Esta página está traducida, pero el contenido de los informes no. Las cifras proceden de registros públicos británicos, y los nombres de lugares, colegios y organismos se dejan tal como aparecen en esos registros, para que pueda contrastar cualquier dato con su fuente. Las cifras se leen igual en cualquier idioma.",
                           "ar": "هذه الصفحة مترجمة، أما محتوى التقارير فلا. الأرقام مصدرها سجلات حكومية بريطانية، وأسماء الأماكن والمدارس والجهات الناشرة تبقى كما وردت في تلك السجلات حتى يمكنك مطابقة أي رقم بمصدره. والأرقام نفسها تُقرأ كما هي بأي لغة.",
                           "fr": "Cette page est traduite, mais le contenu des rapports ne l'est pas. Les chiffres proviennent de registres publics britanniques, et les noms de lieux, d'écoles et des organismes qui publient chaque chiffre restent tels quels, afin que vous puissiez vérifier chaque donnée à la source. Les chiffres se lisent de la même façon dans toutes les langues.",
                           "ja": "このページは翻訳されていますが、レポートの中身は英語のままです。数値は英国政府の記録に基づいており、地名・学校名・各数値の公表機関名は記録どおりに残してあります。出典と照合できるようにするためです。数値そのものは、どの言語でも同じように読めます。",
                           "ko": "이 페이지는 번역되었지만 리포트 내용은 번역되지 않습니다. 수치는 영국 정부 기록에서 가져오며, 지명·학교명·각 수치를 공개한 기관명은 기록 그대로 유지합니다. 출처와 대조해 확인할 수 있도록 하기 위해서입니다. 숫자 자체는 어떤 언어로 읽어도 같습니다."},

    # ---- landing page scaffolding ----
    "landing.how.title":  {"en": "How to use it", "zh-hant": "使用方法", "zh-hans": "使用方法", "hi": "इसका उपयोग कैसे करें", "es": "Cómo usarlo", "ar": "كيفية الاستخدام", "fr": "Comment l'utiliser", "ja": "使い方", "ko": "사용 방법"},
    "landing.how.body":   {"en": "Type a UK postcode into the box above. The report lists what the public record says about that address: what nearby homes sold for and when, the energy rating, flood risk, recorded crime, nearby schools and their Ofsted ratings, broadband and mobile coverage, and the ground itself. Nothing is estimated. Where no reliable public record exists, the report says so instead of guessing.",
                           "zh-hant": "在上方輸入英國郵區編號。報告會列出公開紀錄對該地址的記載：附近成交價與成交日期、能源評級、水浸風險、警方登記罪案、鄰近學校及其 Ofsted 評級、寬頻與流動網絡覆蓋，以及地質狀況。全部不作估算。若無可靠公開紀錄，報告會直接說明，而非猜測。",
                           "zh-hans": "在上方输入英国邮政编码。报告会列出公开记录对该地址的记载：附近成交价与成交日期、能源评级、洪水风险、警方登记案件、邻近学校及其 Ofsted 评级、宽带与移动网络覆盖，以及地质状况。全部不作估算。若无可靠公开记录，报告会直接说明，而非猜测。",
                           "hi": "ऊपर दिए बॉक्स में यूके पोस्टकोड लिखें। रिपोर्ट बताती है कि सार्वजनिक अभिलेख उस पते के बारे में क्या कहते हैं: आसपास के घर कब और कितने में बिके, ऊर्जा रेटिंग, बाढ़ का जोखिम, दर्ज अपराध, आसपास के स्कूल और उनकी Ofsted रेटिंग, ब्रॉडबैंड और मोबाइल कवरेज, और ज़मीन की स्थिति। कुछ भी अनुमानित नहीं है। जहाँ भरोसेमंद सार्वजनिक अभिलेख नहीं है, रिपोर्ट अनुमान लगाने के बजाय यही कहती है।",
                           "es": "Escriba un código postal británico en la casilla de arriba. El informe recoge lo que dice el registro público sobre esa dirección: por cuánto y cuándo se vendieron las viviendas cercanas, la calificación energética, el riesgo de inundación, los delitos registrados, los colegios cercanos y su calificación de Ofsted, la cobertura de banda ancha y móvil, y el propio terreno. Nada se estima. Donde no existe un registro público fiable, el informe lo dice en lugar de suponer.",
                           "ar": "اكتب رمزًا بريديًا بريطانيًا في الحقل أعلاه. يعرض التقرير ما تقوله السجلات العامة عن ذلك العنوان: أسعار وتواريخ بيع المنازل المجاورة، وتصنيف كفاءة الطاقة، وخطر الفيضان، والجرائم المسجلة، والمدارس القريبة وتقييمات Ofsted لها، وتغطية الإنترنت والهاتف المحمول، وطبيعة الأرض نفسها. لا شيء مُقدَّر بالتخمين. وحين لا يوجد سجل عام موثوق، يقول التقرير ذلك بدل التخمين.",
                           "fr": "Saisissez un code postal britannique dans le champ ci-dessus. Le rapport reprend ce que dit le registre public sur cette adresse : prix et dates de vente des logements voisins, classe énergétique, risque d'inondation, délits enregistrés, écoles proches et leur évaluation Ofsted, couverture internet et mobile, et le sol lui-même. Rien n'est estimé. Lorsqu'aucun registre public fiable n'existe, le rapport le dit plutôt que de deviner.",
                           "ja": "上の欄に英国の郵便番号を入力してください。レポートには、その住所について公的記録が示す内容が並びます。近隣の成約価格と成約時期、エネルギー性能評価、洪水リスク、記録された犯罪、近隣校とその Ofsted 評価、ブロードバンドと携帯の通信状況、そして土地そのものの状態です。推計は行いません。信頼できる公的記録がない場合は、推測せずにその旨を記します。",
                           "ko": "위 입력란에 영국 우편번호를 입력하세요. 리포트는 공공 기록이 해당 주소에 대해 말하는 내용을 정리합니다. 인근 주택의 실거래가와 거래 시점, 에너지 등급, 홍수 위험, 신고된 범죄, 인근 학교와 Ofsted 평가, 인터넷·이동통신 커버리지, 그리고 토지 자체의 상태입니다. 추정치는 없습니다. 신뢰할 만한 공공 기록이 없으면 추측하지 않고 그렇다고 밝힙니다."},

    "landing.free.title": {"en": "What it costs", "zh-hant": "收費說明", "zh-hans": "收费说明", "hi": "इसकी लागत", "es": "Qué cuesta", "ar": "التكلفة", "fr": "Ce que cela coûte", "ja": "料金について", "ko": "요금 안내"},
    "landing.free.body":  {"en": "Searching any postcode is free and needs no account. A free account includes one full report with every check unlocked. This site sells nothing else and lists no properties, so it has no reason to make an area look better than the record says it is.",
                           "zh-hant": "搜尋任何郵區免費，亦無需開立帳戶。免費帳戶包含一份完整報告，所有查核項目全部解鎖。本站不出售物業、不刊登樓盤，因此沒有任何理由把一個地區寫得比紀錄更好。",
                           "zh-hans": "搜索任何邮区免费，也无需开立账户。免费账户包含一份完整报告，所有查核项目全部解锁。本站不出售房产、不刊登房源，因此没有任何理由把一个地区写得比记录更好。",
                           "hi": "कोई भी पोस्टकोड खोजना मुफ़्त है और इसके लिए खाता नहीं चाहिए। मुफ़्त खाते में एक पूरी रिपोर्ट शामिल है, जिसमें हर जाँच खुली होती है। यह साइट और कुछ नहीं बेचती और कोई प्रॉपर्टी सूचीबद्ध नहीं करती, इसलिए किसी इलाक़े को अभिलेख से बेहतर दिखाने का कोई कारण नहीं है।",
                           "es": "Buscar cualquier código postal es gratis y no requiere cuenta. Una cuenta gratuita incluye un informe completo con todas las comprobaciones desbloqueadas. Este sitio no vende nada más ni anuncia viviendas, así que no tiene motivo para pintar una zona mejor de lo que dice el registro.",
                           "ar": "البحث عن أي رمز بريدي مجاني ولا يتطلب حسابًا. الحساب المجاني يشمل تقريرًا كاملًا بجميع الفحوصات. هذا الموقع لا يبيع شيئًا آخر ولا يعرض عقارات، فليس لديه سبب ليُظهر منطقة أفضل مما تقوله السجلات.",
                           "fr": "Rechercher un code postal est gratuit et ne demande aucun compte. Un compte gratuit comprend un rapport complet, toutes vérifications débloquées. Ce site ne vend rien d'autre et ne publie aucune annonce, il n'a donc aucune raison d'embellir un quartier au-delà de ce que dit le registre.",
                           "ja": "郵便番号の検索は無料で、アカウントも不要です。無料アカウントには、全項目を解除した完全なレポートが1件含まれます。当サイトは他に何も販売せず、物件広告も掲載しません。ですから、記録が示す以上に地域をよく見せる理由がありません。",
                           "ko": "우편번호 검색은 무료이며 계정이 필요 없습니다. 무료 계정에는 모든 항목이 열린 전체 리포트 1건이 포함됩니다. 이 사이트는 그 밖에 아무것도 판매하지 않고 매물도 게재하지 않습니다. 따라서 기록이 말하는 것보다 지역을 좋게 보이게 할 이유가 없습니다."},

    "landing.cta":        {"en": "Check a postcode", "zh-hant": "查詢郵區", "zh-hans": "查询邮区", "hi": "पोस्टकोड जाँचें", "es": "Consultar un código postal", "ar": "افحص رمزًا بريديًا", "fr": "Vérifier un code postal", "ja": "郵便番号を調べる", "ko": "우편번호 확인하기"},
    "lang.label":         {"en": "Language", "zh-hant": "語言", "zh-hans": "语言", "hi": "भाषा", "es": "Idioma", "ar": "اللغة", "fr": "Langue", "ja": "言語", "ko": "언어"},
    "lang.choose":        {"en": "Choose a language", "zh-hant": "選擇語言", "zh-hans": "选择语言", "hi": "भाषा चुनें", "es": "Elegir idioma", "ar": "اختر لغة", "fr": "Choisir une langue", "ja": "言語を選択", "ko": "언어 선택"},
    "lang.current":       {"en": "Current language", "zh-hant": "目前語言", "zh-hans": "当前语言", "hi": "वर्तमान भाषा", "es": "Idioma actual", "ar": "اللغة الحالية", "fr": "Langue actuelle", "ja": "現在の言語", "ko": "현재 언어"},
    "lang.english_site":  {"en": "English site", "zh-hant": "英文版網站", "zh-hans": "英文版网站", "hi": "अंग्रेज़ी साइट", "es": "Sitio en inglés", "ar": "الموقع بالإنجليزية", "fr": "Site en anglais", "ja": "英語版サイト", "ko": "영문 사이트"},
}


def normalise(code: str | None) -> str:
    """A user-supplied language code mapped onto one we serve, or the
    default. Accepts the regional forms a browser actually sends
    (zh-TW, zh-CN, zh-HK) rather than only our own slugs."""
    if not code:
        return DEFAULT_LANG
    code = code.strip().lower().replace("_", "-")
    if code in LANGUAGES:
        return code
    aliases = {
        "zh": "zh-hans", "zh-cn": "zh-hans", "zh-sg": "zh-hans", "zh-hans-cn": "zh-hans",
        "zh-tw": "zh-hant", "zh-hk": "zh-hant", "zh-mo": "zh-hant", "zh-hant-tw": "zh-hant",
    }
    if code in aliases:
        return aliases[code]
    base = code.split("-")[0]
    return base if base in LANGUAGES else DEFAULT_LANG


def t(key: str, lang: str) -> str:
    """One string. Falls back to English rather than to a blank or a
    key name: a partly-translated language must still render a page a
    reader can use."""
    entry = STRINGS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry["en"]


def catalogue(lang: str) -> dict:
    """Every string for one language, for handing to a template once
    rather than calling t() per string."""
    return {key: t(key, lang) for key in STRINGS}
