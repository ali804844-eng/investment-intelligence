# محفظة الأسهم والصناديق الإسلامية – اختبار الاستراتيجيات

- `data/` أسعار إغلاق أسبوعية (Twelve Data) لـ 13 أصلاً متوافقاً مع الشريعة: SPUS, HLAL, UMMA, AMANX, SPSK, SPRE, AAPL, MSFT, NVDA, TSM, LLY, JNJ, XOM.
- `backtest.py` يختبر 20 استراتيجية على آخر 52 أسبوعاً (بدون تحيز للمستقبل، تكلفة 0.10%) ويحسب اختبار متانة → `results.json`.
- `export_dashboard.py` يحوّل النتائج إلى ملفات `dashboard/*.json` للوحة العرض، و `dashboard/index.html` هي الصفحة.

```bash
python3 backtest.py && python3 export_dashboard.py
```

تحليل بحثي وليس توصية استثمارية.

## الدرس والاستراتيجية الجديدة
- `diagnose.py` أسباب خسائر النصف الثاني.
- `robust_strategy.py` استراتيجية "الدرع التكيفي" (نسختان) مع تشغيل بشرائح وبيانات عائد كلي تقريبية، واختبارات المتانة → `shield_results.json`.
