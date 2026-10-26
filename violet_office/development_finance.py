"""Decimal monthly predictions, using only supplied assumptions, never actual sales."""
from decimal import Decimal,InvalidOperation,ROUND_CEILING,localcontext

PARAMETERS={
 'initial_cost_yen':'初期費用（円）','fixed_cost_yen':'月間固定費（円）','variable_cost_per_sale_yen':'成約1件あたり変動費（円）',
 'price_yen':'成約1件あたり単価（円）','leads_month':'月間見込み客数','conversion_percent':'成約率%','hours_per_sale':'成約1件あたり作業時間','hours_month':'月間利用可能時間'}
SCENARIOS={'pessimistic':'悲観','standard':'標準','optimistic':'楽観'}


def number(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)) or len(str(value))>32: raise ValueError('Finite explicit decimal required')
    try: d=Decimal(str(value))
    except InvalidOperation: raise ValueError('Decimal required') from None
    if not d.is_finite() or d<0 or d>1000000000 or d.as_tuple().exponent< -4: raise ValueError('Bounded nonnegative decimal required')
    return d


def show(value):
    if value is None: return None
    return format(value.quantize(Decimal('.01')),'f')


def calculate(parameters):
    if not isinstance(parameters,dict) or set(parameters)-set(PARAMETERS): raise ValueError('Known assumption fields only')
    values={k:number(v) for k,v in parameters.items() if v not in ('',None)}
    if values.get('conversion_percent',Decimal(0))>100: raise ValueError('Conversion must be 0..100 percent')
    missing=[k for k in PARAMETERS if k not in values]
    formulas={'sales':'月間見込み客数 × 成約率 ÷ 100（期待件数、小数可）','revenue':'期待成約件数 × 単価','variable_cost':'期待成約件数 × 1件あたり変動費','gross_profit':'売上 − 変動費','operating_profit':'粗利益 − 月間固定費','break_even_sales':'ceil(月間固定費 ÷ (単価 − 1件あたり変動費))','payback':'初期費用 ÷ 月次営業利益（正の場合、一定継続の仮定）','hours':'期待成約件数 × 1件あたり作業時間'}
    base={'mode':'prediction_from_assumptions','currency':'JPY','period':'month','formulas':formulas,'missing':missing,'actual_revenue':None,'exclusions':'税・借入・金利・減価償却・季節性・回収不能・運転資金の増減は未計算。単純回収期間はキャッシュフロー分析ではない。収益保証なし。'}
    if missing: return {**base,'status':'uncomputed','results':None}
    with localcontext() as context:
        context.prec=40
        p=values; sales=p['leads_month']*p['conversion_percent']/100
        revenue=sales*p['price_yen']; variable=sales*p['variable_cost_per_sale_yen']; gross=revenue-variable; operating=gross-p['fixed_cost_yen']; margin=p['price_yen']-p['variable_cost_per_sale_yen']; hours=sales*p['hours_per_sale']
        be=(p['fixed_cost_yen']/margin).to_integral_value(rounding=ROUND_CEILING) if margin>0 else None
        payback=p['initial_cost_yen']/operating if operating>0 else None
        warnings=[]
        if margin<=0: warnings.append('限界利益が正ではなく、有限の損益分岐点は算定不可')
        if operating<=0: warnings.append('月次営業利益が正ではなく、初期費用の回収期間は算定不可')
        if hours>p['hours_month']: warnings.append('想定作業時間が利用可能時間を超える。成約件数を自動補正していない')
        if p['hours_per_sale']==0: warnings.append('1件あたり作業時間0は入力者の仮定。実際の無人稼働を確認したものではない')
        return {**base,'status':'computed_prediction','assumptions':{k:str(v) for k,v in values.items()},'results':{'expected_sales':show(sales),'revenue_yen':show(revenue),'variable_cost_yen':show(variable),'gross_profit_yen':show(gross),'operating_profit_yen':show(operating),'break_even_units':str(be) if be is not None else None,'break_even_revenue_yen':show(be*p['price_yen']) if be is not None else None,'break_even_leads':str((be/(p['conversion_percent']/100)).to_integral_value(rounding=ROUND_CEILING)) if be is not None and p['conversion_percent']>0 else None,'payback_months':show(payback),'required_hours':show(hours),'within_hours':hours<=p['hours_month']},'warnings':warnings}
