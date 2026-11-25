# دليل التثبيت والتشغيل — TITAN AI PREMIUM (البناء التأسيسي)

> هذا بناء قابل للتشغيل والاختبار محليًا مع وظائف ما زالت جزئية. **لا يفعّل التداول الحقيقي**. دورة Demo تستخدم شموع Binance Public للقراءة فقط، وأوامرها داخلية Shadow ولا تتصل بحساب Binance.

## المتطلبات
- Python 3.11/3.12 موصى به (البيئة الحالية: Python 3.14 مع توافق Starlette runtime).
- Node.js/npm لبناء الواجهة (بناء Vite قد يتطلب بيئة Node قياسية؛ في بيئة Android هذه تعثر بناء Rollup الأصلي).

## 1) إعداد الخلفية (Backend)
```bash
cd /path/to/workspace
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

انسخ الإعدادات (بدون أسرار):
```bash
cp .env.example .env
```

عيّن كلمة المرور الأولى محليًا (لا ترسلها في المحادثة):
```bash
cd backend
# اختياري:
# export TITAN_ALLOWED_EMAILS=abdoavm02@gmail.com
python3 -m app.scripts.set_initial_password --email abdoavm02@gmail.com
```

فعّل المصادقة عند الحاجة:
```bash
export TITAN_AUTH_REQUIRED=true
```

شغّل الخادم (Starlette runtime المتوافق):
```bash
cd backend
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 2) الواجهة (Frontend)
```bash
cd frontend
npm install
npm run dev
```
Vite يوجّه `/api` إلى `http://127.0.0.1:8000` افتراضيًا.

إذا فشل بناء Rollup في بيئة Android/مسار خاص، نفّذ البناء على بيئة Node سطح مكتب/خادم قياسية.

## 3) تشغيل التداول التجريبي المستمر (Spot Demo/Shadow)\n\nبعد إعداد قاعدة البيانات وتشغيل الخادم مرة واحدة على الأقل، افتح جلسة Termux ثانية وشغّل:\n\n```bash\ncd backend\npython3 -m app.scripts.run_demo_loop --interval 300 --symbols BTC/USDT\n```\n\n- ينفّذ دورة تحليل كل 5 دقائق افتراضيًا باستخدام شموع Binance Public للقراءة فقط.\n- لا يحتاج API Key، ولا يرسل أي أمر إلى Binance؛ BUY المقبول يتحول إلى أمر Shadow داخل قاعدة بيانات TITAN فقط.\n- يمكنك إضافة رموز مفصولة بفواصل، مثل `BTC/USDT,ETH/USDT`، مع الانتباه لحدود الطلبات العامة.\n- للإيقاف: `Ctrl+C` أو زر `STOP BOT` أو `EMERGENCY STOP` من الواجهة.\n- لا تتركه يعمل على أنه تداول حقيقي؛ النتائج محاكاة، وقد تكون النتيجة `WAIT` دون فتح أي صفقة.\n- التشغيل المستمر هنا سكربت CLI مستقل؛ لا يبدأ تلقائيًا من زر Master Start، ويجب تشغيله في بيئتك المحلية.\n\nلتجربة دورة واحدة فقط:\n\n```bash\npython3 -m app.scripts.run_demo_loop --once\n```\n\n## 4) الاختبارات المنفذة فعليًا (في هذا البناء)
```bash
cd backend
python3 -m pytest -v
```
آخر نتائج CI موثقة: Backend tests وFrontend Vite build وDemo Shadow smoke نجحت في [تشغيل CI](https://github.com/unkn11527-ux/titan-ai-premium/actions/runs/42476562353). كما نجح فحص compileall في التشغيل نفسه.

## 5) ربط Binance/Telegram/AI (لاحقًا، Demo/Testnet أولًا)
- لا تضع مفاتيح API/رموز Telegram/كلمات مرور في الكود أو GitHub.
- Binance: صلاحيات قراءة/تداول حسب الحاجة فقط؛ **ممنوع السحب/التحويل**.
- ابدأ بـ Demo/Testnet، تحقق من الأهلية/KYC/القيود الجغرافية/شروط Binance الرسمية، ثم راجع قبل أي Live.
- Live يتطلب تمكينًا صريحًا وتأكيدًا ومراجعة امتثال/مخاطر؛ لا انتقال تلقائي Demo→Live.

## 6) الأمان
- سجلات وكلمات المرور: تُخزّن كلمات المرور كتجزئة PBKDF2 مع salt؛ لا تُسجّل القيم الحساسة.
- محرك الامتثال له الأولوية ولا يضمن امتثالًا قانونيًا/تنظيميًا؛ يطلب تحققًا بشريًا عند عدم الوضوح.

## 7) النسخ الاحتياطي
- قاعدة بيانات SQLite الافتراضية: `backend/data/titan.db` (مسار قابل للتهيئة).
- احتفظ بنسخ من DB والإعدادات غير السرية؛ اختبر الاستعادة قبل الاعتماد.
