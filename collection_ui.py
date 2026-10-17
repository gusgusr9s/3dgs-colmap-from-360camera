"""Gallery search before pagination and shared accessible action group markup."""
import html
import re
from urllib.parse import quote


def action_group(label, content):
    return f'<div class="action-group" role="group" aria-label="{html.escape(label,quote=True)}"><span class="action-group-label">{html.escape(label)}</span><div class="actions">{content}</div></div>' if content else ''


def gallery_items(items, query='', order='frame-asc', metadata=None):
    def parts(item):
        return item if isinstance(item,tuple) else (item,(metadata or {}).get(item.name,{}))
    def text(item):
        image,frame=parts(item)
        return ' '.join([image.name,*[str(frame.get(k,'')) for k in ('frame_index','yaw_deg','pitch_deg')]])
    query=query.strip().casefold()
    result=[item for item in items if not query or query in text(item).casefold()]
    def natural(value):
        return [(0,int(part)) if part.isdigit() else (1,part.casefold()) for part in re.split(r'(\d+)',value)]
    def key(item):
        image,frame=parts(item)
        if order.startswith('frame-'):
            index=frame.get('frame_index')
            if index is not None:return (0,float(index),natural(image.name))
        return (1,0,natural(image.name))
    return sorted(result,key=key,reverse=order.endswith('-desc'))


def gallery_tools(url, anchor, query, order, matched, total):
    options=''.join(f'<option value="{key}"{" selected" if key==order else ""}>{label}</option>' for key,label in
                    [('frame-asc','เฟรมแรก → สุดท้าย'),('frame-desc','เฟรมสุดท้าย → แรก'),('name-asc','ชื่อ A–Z'),('name-desc','ชื่อ Z–A')])
    clear=f'<a class="button" href="{url}#{anchor}">ล้างค้นหา</a>' if query else ''
    return f'''<form class="collection-toolbar" role="search" method="get" action="{url}#{anchor}">
<label>ค้นหาภาพ<input type="search" name="q" value="{html.escape(query,quote=True)}" placeholder="ชื่อภาพ เลขเฟรม หรือมุมภาพ"></label>
<label>เรียงตาม<select name="sort">{options}</select></label><button type="submit">ค้นหา</button>{clear}
<span role="status" class="muted">{matched:,} / {total:,} ภาพ · ค้นหาทั้งชุด</span></form>'''
