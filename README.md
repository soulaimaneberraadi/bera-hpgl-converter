# BERA Converter — HPGL/PLT → AAMA DXF (Gerber PDS ready)

محوّل ملفات الطراسور (HPGL/PLT) إلى قطع مستقلّة جاهزة للاستيراد في **Gerber AccuMark / PDS** (وأيضًا Lectra, Optitex). يقوم بهندسة عكسية للمخطط: يفكّك القطع، يستخرج الأسماء والمقاسات والإشارات (crans)، ويصدّرها بصيغة **ASTM D6673 / AAMA DXF**.

## المزايا
- **AAMA DXF** ببنية BLOCKS + ASTM XDATA — كل قطعة قابلة للقصّ في Gerber، باسمها ومقاسها وطبقة الإشارات (4).
- **هندسة عكسية**: الموديل، المقاس، الكمية، الطول، المردود (rendement)، العرض (laize).
- **ربط هندسي للتسميات** (اسم/مقاس صحيح لكل قطعة).
- **فلترة حسب المقاس** (كل المقاسات أو مقاس واحد).
- تصدير: AAMA DXF · DXF · SVG · PLT · CSV (تقرير).

## التشغيل محليًا
```bash
python run_local.py    # ثم افتح http://127.0.0.1:9000
```

## النشر على Vercel
1. استورد هذا المستودع في [vercel.com/new](https://vercel.com/new).
2. اتركه بالإعداد الافتراضي (Python serverless عبر `api/index.py` + `vercel.json`).
3. **Deploy** — ستحصل على رابط حيّ خلال دقيقة.

المكدّس: Python 3 (المكتبة القياسية فقط، بلا اعتماديات) + Tailwind (CDN).
