import json,sys
D=sys.argv[1]
seg=json.load(open(D+'/grid_segments.geojson'));nodes=json.load(open(D+'/grid_nodes.geojson'))
ctx=json.load(open(D+'/scenario_context.geojson'));cfg=json.load(open(D+'/scenario_public_config.json'))
keep={'segment_id','voltage_kv','circuits','length_km','from_node','to_node','component_id'}
for f in seg['features']: f['properties']={k:v for k,v in f['properties'].items() if k in keep}
for f in nodes['features']: f['properties']={k:f['properties'][k] for k in ('node_id','node_type','name')}
data=json.dumps({'segs':seg,'nodes':nodes,'ctx':ctx,'reports':cfg['public_context']['reports']},separators=(',',':'))
t=open('template.html').read().replace('/*DATA*/',data).replace('/*ENGINE*/',open('engine.js').read())
open('index.html','w').write(t);print(len(t))
