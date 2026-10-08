"""Shared dependency-free hover UI for locally generated HTML/SVG reports."""
import html
import json


def point_attributes(points):
    """Embed bounded displayed observations as x/y/text, safely in an attribute."""
    return ' data-mbt-points="' + html.escape(json.dumps(points, ensure_ascii=True,
                                                       allow_nan=False), quote=True) + '"'


HOVER_SCRIPT = r'''<script>
(()=>{
  const cache=new WeakMap();
  const tip=document.createElement('div');tip.className='mbt-hover-tip';
  tip.setAttribute('role','tooltip');tip.hidden=true;document.body.append(tip);
  const style=document.createElement('style');style.textContent=`
  .mbt-hover-tip{position:fixed;z-index:10000;pointer-events:none;white-space:pre-line;
  max-width:min(360px,calc(100vw - 16px));padding:10px 13px;border:1px solid #456580;
  border-radius:8px;background:#07121ff5;color:#edf5ff;font:12px/1.6 system-ui,sans-serif;
  box-shadow:0 6px 24px #0008;font-variant-numeric:tabular-nums}
  .mbt-hover-tip[hidden]{display:none}@media print{.mbt-hover-tip{display:none}}`;
  document.head.append(style);
  let activeLine=null;
  function hide(){tip.hidden=true;if(activeLine)activeLine.setAttribute('visibility','hidden');}
  function show(text,e){tip.textContent=text;tip.hidden=false;
    const box=tip.getBoundingClientRect();
    tip.style.left=Math.max(8,Math.min(e.clientX+16,innerWidth-box.width-8))+'px';
    tip.style.top=Math.max(8,Math.min(e.clientY+16,innerHeight-box.height-8))+'px';}
  document.addEventListener('pointermove',e=>{
    const target=e.target instanceof Element?e.target:null;
    const svg=target?.closest('svg[data-mbt-points]');
    if(svg){
      let data=cache.get(svg);
      if(!data){try{data=JSON.parse(svg.getAttribute('data-mbt-points'));}catch{hide();return;}
        cache.set(svg,data);}
      if(!data.length){hide();return;}
      const matrix=svg.getScreenCTM();if(!matrix){hide();return;}
      const cursor=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());
      if(cursor.x<data[0][0]||cursor.x>data[data.length-1][0]){hide();return;}
      let lo=0,hi=data.length-1;
      while(lo<hi){const mid=Math.floor((lo+hi)/2);if(data[mid][0]<cursor.x)lo=mid+1;else hi=mid;}
      let index=lo;if(index>0&&Math.abs(data[index-1][0]-cursor.x)<Math.abs(data[index][0]-cursor.x))index--;
      // Same timestamps can contain several deals: choose the nearest plotted value.
      let start=index;while(start>0&&data[start-1][0]===data[index][0])start--;
      for(let i=start;i<data.length&&data[i][0]===data[index][0];i++)
        if(Math.abs(data[i][1]-cursor.y)<Math.abs(data[index][1]-cursor.y))index=i;
      if(activeLine&&activeLine.ownerSVGElement!==svg)activeLine.setAttribute('visibility','hidden');
      let line=svg.querySelector('.mbt-hover-line');
      if(!line){line=document.createElementNS(svg.namespaceURI,'line');
        line.setAttribute('class','mbt-hover-line');line.setAttribute('stroke','#9fb6cc');
        line.setAttribute('stroke-dasharray','4 4');line.setAttribute('pointer-events','none');svg.append(line);}
      line.setAttribute('x1',data[index][0]);line.setAttribute('x2',data[index][0]);
      line.setAttribute('y1','24');line.setAttribute('y2',svg.viewBox.baseVal.height-50);
      line.setAttribute('visibility','visible');activeLine=line;
      show(data[index][2],e);return;
    }
    const cell=target?.closest('[data-mbt-tooltip]');
    if(cell){hide();show(cell.getAttribute('data-mbt-tooltip'),e);return;}
    const mark=target?.closest('svg rect,svg path,svg circle,svg polygon,svg polyline');
    const title=mark?.querySelector('title');
    if(title){hide();show(title.textContent,e);return;}
    hide();
  });
  document.addEventListener('pointerout',e=>{if(!e.relatedTarget)hide();});
  document.addEventListener('scroll',hide,true);window.addEventListener('blur',hide);
})();
</script>'''
