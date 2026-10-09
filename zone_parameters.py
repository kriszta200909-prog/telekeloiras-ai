"""Decode an inscription using the downloaded official parameter legend.

No expected zone, cadastral number or numeric building parameter is embedded.
Ambiguous tables and unsupported code forms remain unparsed.
"""
import re
import unicodedata


def compact(value):
    value=unicodedata.normalize('NFD',value).casefold()
    return ''.join(c for c in value if c.isalnum() and not unicodedata.combining(c))


ORDINALS=('elso','masodik','harmadik','negyedik','otodik')
PARAMETERS=('Legnagyobb épületmagasság','Beépítési mód','Legnagyobb beépítettség',
            'Legkisebb zöldfelület','Kialakítható legkisebb telekterület')


def legend_tables(text):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    headers=[(i,compact(line)) for i,line in enumerate(lines)
             if compact(line).startswith('epitesiovezetikod')]
    tables={}
    for ordinal in ORDINALS:
        matches=[(i,h) for i,h in headers if h=='epitesiovezetikod'+ordinal+'szamajele']
        if len(matches)!=1:raise ValueError('Hiányzó vagy többszörös paraméteroszlop: '+ordinal)
        start=matches[0][0];stop=min([i for i,h in headers if i>start],default=len(lines))
        content=lines[start+1:stop]
        positions=[(i,line) for i,line in enumerate(content) if re.fullmatch('[0-9]',line)]
        if len(positions)<2 or len({key for i,key in positions})!=len(positions):
            raise ValueError('Hiányos vagy ismétlődő paraméterkulcsok: '+ordinal)
        rows={}
        for j,(i,key) in enumerate(positions):
            end=positions[j+1][0] if j+1<len(positions) else len(content)
            values=content[i+1:end]
            # X/Y/Z starts a separate special-rule group, not the value of 0.
            special=next((k for k,v in enumerate(values) if compact(v)=='xyz'),len(values))
            value=' '.join(values[:special]).strip()
            if not value:raise ValueError('Üres paraméterérték: '+ordinal+'/'+key)
            rows[key]=value
        tables[ordinal]=rows
    return tables


def decode_zone(code, tables):
    match=re.fullmatch(r'([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű-]+)-([0-9])([0-9])\.([0-9])([0-9])\.([0-9])',code or '')
    if not match:return []
    if any(digit not in tables[ordinal] for ordinal,digit in zip(ORDINALS,match.groups()[1:])):
        raise ValueError('A kód egyik paramétere nincs a forrás jelmagyarázatában.')
    return [{'Előírás':name,'Érték':tables[ordinal][digit],
             'Kódpozíció':i,'Forráskulcs':digit}
            for i,(name,ordinal,digit) in enumerate(zip(PARAMETERS,ORDINALS,match.groups()[1:]),1)]
