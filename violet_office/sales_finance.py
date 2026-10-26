"""Explicit per-line JPY tax rounding, no assumed tax classification or rate."""
from decimal import Decimal,ROUND_DOWN,ROUND_HALF_UP,ROUND_UP
from .development_finance import number
from .portfolio import text

ROUNDING={'floor':ROUND_DOWN,'half_up':ROUND_HALF_UP,'ceil':ROUND_UP}
TAX={'unknown':'未確認','included':'税込','excluded':'税別','exempt':'税額0の区分（法的根拠は代表確認）'}


def quote_totals(lines,policy):
    if not isinstance(lines,list) or not 1<=len(lines)<=50: raise ValueError('1..50 quote lines required')
    kind=policy.get('tax_type','unknown'); rounding=policy.get('rounding'); rate=number(policy['rate']) if policy.get('rate') not in ('',None) else None
    if kind not in TAX or rate is not None and rate>100: raise ValueError('Tax type/rate required')
    normalized=[]
    for line in lines:
        if set(line)!={'label','quantity','unit_yen'}: raise ValueError('Known line fields required')
        quantity=number(line['quantity']); unit=number(line['unit_yen'])
        if quantity<=0: raise ValueError('Positive quantity required')
        normalized.append({'label':text(line['label'],160),'quantity':str(quantity),'unit_yen':str(unit)})
    ready=bool(policy.get('confirmed') and kind!='unknown' and rounding in ROUNDING and rate is not None)
    if kind=='exempt' and rate not in (None,Decimal(0)): raise ValueError('Exempt rate must be explicitly zero')
    if not ready: return {'lines':normalized,'policy':dict(policy),'totals':None,'state':'tax_confirmation_required','unit':'JPY','rounding_scope':'per_line'}
    rd=ROUNDING[rounding]; net=tax=gross=0; detailed=[]
    for line in normalized:
        amount=(number(line['quantity'])*number(line['unit_yen'])).quantize(Decimal(1),rounding=rd)
        if kind=='included': line_gross=int(amount); line_tax=int((amount*rate/(100+rate)).quantize(Decimal(1),rounding=rd)); line_net=line_gross-line_tax
        else: line_net=int(amount); line_tax=int((amount*rate/100).quantize(Decimal(1),rounding=rd)) if kind=='excluded' else 0; line_gross=line_net+line_tax
        net+=line_net; tax+=line_tax; gross+=line_gross
        detailed.append({**line,'net_yen':line_net,'tax_yen':line_tax,'gross_yen':line_gross})
    if gross>1000000000000: raise ValueError('Quote amount exceeds supported bound')
    return {'lines':detailed,'policy':dict(policy),'totals':{'net_yen':net,'tax_yen':tax,'gross_yen':gross},'state':'computed_draft','unit':'JPY','rounding_scope':'per_line','notice':'代表が税区分・税率・行別端数処理を指定。税務適合やインボイス要件は保証しない。'}
