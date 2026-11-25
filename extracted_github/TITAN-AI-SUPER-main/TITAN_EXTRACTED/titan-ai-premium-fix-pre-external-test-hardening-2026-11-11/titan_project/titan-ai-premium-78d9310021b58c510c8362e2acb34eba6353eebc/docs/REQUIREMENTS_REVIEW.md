# مراجعة المتطلبات الإضافية (مقارنة بالمواصفات السابقة والأساس المنفذ)

> هذه الوثيقة **مراجعة/تصنيف** للمتطلبات الجديدة المرسلة لاحقًا. لا تعني أن كل بند منفذ. أي تنفيذ جديد يحتاج موافقة صريحة خاصة عند المساس بالأمان، التداول الحقيقي، الأسرار، أو البنية.

## ملخص الحالة الحالية (الأساس المنفذ فعليًا)
- **موجود ومختبر جزئيًا:** فصل Demo/Live مع فرض تنفيذ Spot فقط، حالة نظام وسجل تدقيق، Binance Public read-only متعدد الأطر (1D/4H/1H/15M/5M) وSynthetic/Labeled للتطوير، تحليل فني/شموع/هيكل سوق heuristic، Shadow Spot محاسبي برصيد Demo 150 USDT ومخاطرة 0.5%، حد خسارة يومي محقق 5% يوقف إدخالات جديدة، Backtesting تاريخي أساسي برسوم/انزلاق، وعامل CLI لدورات Demo مستمرة محليًا. اختبارات CI: 62 passed, 1 skipped في آخر تشغيل Backend.
- **غير منفذ/خارجي:** مزامنة حساب Binance وأوامر حقيقية؛ Telegram وAI providers والأخبار الحية؛ Order Book/Order Flow موثوق؛ Walk-Forward/OOS كامل؛ مصادقة إنتاجية 2FA؛ دمج العامل المستمر في Master Start/Stop؛ استضافة 24/7 وAPK مختبر.

## تصنيف المتطلبات الإضافية

| المجموعة | أمثلة | التصنيف | ملاحظات الدمج/التنفيذ |
|---|---|---|---|
| منع تكرار الأوامر/مزامنة Binance/أوامر جزئية/رموز التداول/انزلاق حي | Pre-trade checks, idempotency, symbol filters, order reconciliation | **غير موجودة وتستحق الإضافة** (حرجة قبل Live) | تُدمج في `execution + exchange adapter + order gateway`؛ تحتاج Binance API/Testnet. |
| طوارئ/استعادة بعد انقطاع/مطابقة الحالة | circuit breaker, recovery, reconciliation before resume | **جزئيًا مفاهيميًا** (حالات/سجلات) / **ناقص تنفيذيًا** | يتطلب خدمات خارجية ومراقبة خادم. |
| سجل أوامر كامل | send/response/fill/qty/price/fees/status | **غير موجود** | يُضاف كنموذج `OrderEvent/OrderRecord` مرتبط بـ Binance IDs. |
| تمييز اختراق حقيقي/وهمي، دفتر أوامر/تدفق، تلاعب، سيناريوهات | SMC/order flow, scenarios, confidence calibration | **جزئيًا** (هيكل/heuristics بسيطة) / **ناقص** | دفتر الأوامر/التدفق يحتاج بيانات Binance؛ التلاعب يُعرض كاشتباه بدرجة ثقة. |
| عدم الاعتماد على مؤشر/نموذج واحد، WAIT عند عدم الوضوح | ensemble, conflict resolution | **جزئيًا** (توليف أطر + WAIT) | يحتاج محرك تعارض/أوزان أدلة. |
| حدود خسائر يومية/أسبوعية/شهرية، drawdown، ارتباط العملات، مخاطر محفظة | portfolio/risk budget | **ناقص** | يُدمج في `risk_management + portfolio_management`. |
| التعلم المقاس/سجل قرارات قبل النتيجة/Shadow/Canary/تجارب | prediction before outcome, experiments, canary | **ناقص** (سجلات تعلم أساسية موجودة) | يحتاج نماذج تجارب/توقعات/إصدارات استراتيجية. |
| حوكمة نماذج AI/Model drift/سجل نموذج لكل قرار | model registry, evaluation | **ناقص**؛ **يحتاج خدمة خارجية** عند استخدام مزودين | يُدمج في `integrations/ai + audit`. |
| أمن متقدم (Prompt injection, poisoning, RBAC, secrets, dependency scanning) | agent security, API protection | **جزئيًا** (سياسة أسرار) / **ناقص** | حماية أسرار إلزامية؛ اختبارات أمنية قبل إصدارات مهمة. |
| CI/CD، بيئات منفصلة، rollback، release notes | Dev/Test/Prod, versioning | **ناقص** | يحتاج Git repo + بيئة استضافة/اختبار. |
| RTO/RPO، مراقبة موارد، استعادة خادم بديل | ops/SRE | **يحتاج خدمة خارجية** | لا يُدّعى تشغيل 24/7 بدون استضافة. |
| تخصيص رأس مال/تكلفة فرصة/ميزانية تشغيلية | capital efficiency, ops budget | **ناقص** | مفيد للنسخة المتقدمة. |
| جودة تنفيذ (TWAP/VWAP/latency/slippage analytics) | smart execution | **ناقص**؛ يعتمد على دعم Binance والبيانات | يُدمج في `execution policy`. |
| اختبارات سيناريوهات/ضغط/بيانات خاطئة | stress, chaos, bad data | **ناقص** | يمكن تنفيذ محاكاة محلية جزئية؛ الكامل يحتاج بيئات. |
| مخاطر عملة/مشروع (delisting, depeg, unlocks, network issues) | asset risk | **ناقص**؛ **يحتاج مصادر خارجية موثوقة** | تصنيف مخاطر مع درجة ثقة. |
| Cross-market/BTC dominance/cycles/options IV | macro/cross-asset | **مناسب للتطوير المستقبلي**؛ **يحتاج مصادر خارجية** | لا يُتخذ قرار على ارتباط غير مثبت. |
| Futures متقدم (liquidation/margin modes/funding impact) وEarn متقدم | futures/earn engines | **جزئي**؛ يحتاج تحقق Testnet | أُضيف طلب ISOLATED قبل فتح مراكز Futures Testnet؛ ما زال التحقق الخارجي وحساب التصفية/التمويل المطابق لـBinance وEarn المتقدم غير مكتمل. |
| تقويم اقتصادي/أخبار مؤكد vs إشاعة | news verification | **ناقص**؛ **يحتاج مصادر خارجية** | تسجيل مصدر/وقت/تأثير. |
| صلاحيات/قراءة فقط/موافقة تغييرات حساسة | permissions, read-only, approvals | **ناقص** (أساس تسجيل مطلوب) | يُدمج مع auth وحوكمة الإعدادات. |
| TITAN SELF-AUDIT ENGINE | independent monitoring | **ناقص** (يمكن تسجيله كمحرك/خدمة) | يجب أن يكون مستقلًا منطقيًا قدر الإمكان. |
| محاسبة مالية/تقارير ضريبية/CSV-Excel-PDF | ledger, realized/unrealized, exports | **ناقص** | يحتاج نماذج محاسبية دقيقة ومطابقة Binance. |
| حوكمة إعدادات/سجل تغييرات/rollback config | config governance | **ناقص** | **أولوية عالية** للأمان والاستقرار. |
| تفعيل تدريجي/Shadow/Canary/تكافؤ بيئات | deployment strategy | **ناقص**؛ جزئي يحتاج استضافة | Shadow يمكن محليًا/تجريبيًا. |
| بوابة أوامر مستقلة/مراقبة سلوك ما بعد التداول | pre/post-trade controls | **ناقص** (حرج قبل التنفيذ الحقيقي) | يمنع تجاوز LLM/المحللات. |
| سلامة تدفق بيانات لحظي/WebSocket/freshness | stream integrity | **ناقص**؛ **يحتاج Binance streams** | عقود/مؤشرات جودة قابلة للإضافة الآن. |
| أحداث الأصول الرقمية/Token events | forks, airdrops, swaps, unlocks | **ناقص**؛ **يحتاج مصادر خارجية** | لا تُحسب كأرباح تداول عادية. |
| قابلية إعادة إنتاج القرار | replayable decisions | **ناقص** | إضافة لقطات بيانات/إعدادات/إصدارات لكل قرار. |
| تنبيهات موثوقة متعددة القنوات | alert delivery | **ناقص**؛ Telegram خارجي | نموذج أولويات/محاولات/سجل تسليم. |
| امتثال البلد/الخدمة | jurisdiction | **يحتاج دراسة قانونية/خدمية** | لا تُقدم نصائح قانونية/ضريبية. |
| i18n عربي/EN، RTL، PWA، وضوح Demo/Live | UX/accessibility | **جزئيًا** (واجهة أولية) | تحسين تدريجي. |
| تسجيل دخول خاص (بريد واحد) + خروج + جلسات + 2FA + استعادة كلمة مرور | private access | **ناقص** | **تنفيذ آمن فقط**: تجزئة كلمة مرور، allowlist قابل للتهيئة، بدون أسرار في الكود. البريد المطلوب يمكن ضبطه افتراضيًا عبر الإعداد الآمن/`.env` المحلي، وليس كلمة مرور ثابتة. |
| Telegram للتحكم والمراقبة (خاص) | bot control | **ناقص**؛ **يحتاج Bot Token + Chat/User ID** | أوامر حساسة تتطلب تحقق وصلاحيات وحدود مخاطر. |
| APK Android | mobile app package | **يحتاج أدوات بناء Android** | بديل واقعي: PWA قابلة للتثبيت؛ APK فقط إذا بُني واختُبر فعليًا. |
| استضافة 24/7 + نسخ احتياطي + استعادة | continuous ops | **يحتاج خدمة خارجية** | لا يمكن الادعاء به دون تنفيذ واختبار. |
| لوحة 25 قسمًا كاملة متصلة بالكامل | full dashboard | **جزئيًا** | الهيكل موجود؛ العديد من الأقسام يحتاج محركات/بيانات حقيقية. |

## الوظائف المكررة التي يجب دمجها (لا محركات منفصلة بلا حاجة)
- **Pre-trade checks / order gateway / idempotency / symbol filters / expiry** → وحدة تنفيذ/بوابة أوامر واحدة.
- **Config governance / approvals / change history / rollback** → خدمة حوكمة إعدادات واحدة.
- **Self-audit / monitoring / incident / degradation** → طبقة مراقبة وتدقيق مترابطة (مع استقلال منطقي للتدقيق).
- **Shadow / canary / experiments / backtest / OOS / walk-forward** → دورة حياة استراتيجية وتجارب موحدة.
- **Knowledge / memory / negative knowledge / reproducibility / source reliability** → قاعدة معرفة وسجلات موثوقة موحدة.
- **Alerts / Telegram / reports** → نظام إشعارات وتقارير موحد مع أولويات وقنوات.

## قرار تنفيذي مقترح (بدون تجاوز الموافقة)
1. **تنفيذ الآن (آمن محليًا):** حوكمة إعدادات وسجل تغييرات، حقول قابلية إعادة إنتاج القرار، نماذج تجارب/توقعات/إصدارات استراتيجية (أساس)، محرك تدقيق ذاتي مسجل، أساس مصادقة خاصة آمنة (allowlist + hash + sessions)، محولات Binance/Telegram/AI بالواجهات والـ mock، تحسين PWA/حالات صادقة.
2. **بعد توفير مفاتيح Demo/Testnet:** مزامنة Binance، دفتر أوامر/سيولة، بوابة أوامر حية، سجلات تنفيذ، تنبيهات Telegram حقيقية.
3. **بعد استضافة مناسبة:** تشغيل 24/7، نسخ احتياطي/استعادة مختبرة، مراقبة موارد، صفحات حالة، APK/PWA إنتاجية.

## ما لن أفعله دون موافقة صريحة إضافية
- تخزين كلمة مرور كنص عادي أو وضع أسرار في الكود/GitHub.
- تفعيل Live أو الانتقال التلقائي من Demo إلى Live.
- منح صلاحيات سحب/تحويل.
- ادعاء اكتمال اختبار/استضافة/APK/ربط حي غير منفذ فعليًا.
