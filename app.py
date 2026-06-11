from flask import Flask, request, jsonify, render_template, Response
import requests
import json
import base64
import os
import glob

app = Flask(__name__)
PROFILES_DIR = os.path.join(os.path.dirname(__file__), 'profiles')

def load_builtin_profiles():
    profiles = []
    for path in sorted(glob.glob(os.path.join(PROFILES_DIR, '*.json'))):
        try:
            with open(path) as f:
                p = json.load(f)
                p['_builtin'] = True
                p['_filename'] = os.path.basename(path)
                profiles.append(p)
        except Exception as e:
            print(f"Error loading profile {path}: {e}")
    return profiles

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/profiles', methods=['GET'])
def get_profiles():
    return jsonify(load_builtin_profiles())

@app.route('/api/profiles/<filename>', methods=['PUT'])
def save_profile(filename):
    if not filename.endswith('.json') or '/' in filename or '..' in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    path = os.path.join(PROFILES_DIR, filename)
    data = request.json
    data.pop('_builtin', None)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})

@app.route('/api/profiles/<filename>', methods=['DELETE'])
def delete_profile(filename):
    if not filename.endswith('.json') or '/' in filename or '..' in filename:
        return jsonify({'error': 'Invalid filename'}), 400
    path = os.path.join(PROFILES_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
        return jsonify({'ok': True})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/profiles/new', methods=['POST'])
def new_profile():
    data = request.json
    name = data.get('name', 'profile').strip()
    filename = name.lower().replace(' ', '_').replace('/', '_') + '.json'
    path = os.path.join(PROFILES_DIR, filename)
    # avoid overwrite
    base, ext = os.path.splitext(filename)
    i = 1
    while os.path.exists(path):
        filename = f"{base}_{i}{ext}"
        path = os.path.join(PROFILES_DIR, filename)
        i += 1
    data['_filename'] = filename
    clean = {k: v for k, v in data.items() if not k.startswith('_')}
    with open(path, 'w') as f:
        json.dump(clean, f, indent=2)
    return jsonify({'ok': True, 'filename': filename})

@app.route('/api/chat', methods=['POST'])
def chat():
    data     = request.json
    config   = data.get('config', {})
    messages = data.get('messages', [])
    api_type = config.get('api_type', 'openai')

    headers = {'Content-Type': 'application/json'}
    if config.get('bearer_token'):
        headers['Authorization'] = f"Bearer {config['bearer_token']}"
    if config.get('api_key'):
        headers['x-ns-aig-apikey'] = config['api_key']
    for k, v in (config.get('extra_headers') or {}).items():
        if k and v:
            headers[k] = v

    ssl_verify = config.get('ssl_verify', False)

    # ── Gemini ──────────────────────────────────────────────────
    if api_type == 'gemini':
        host  = config.get('host', '').rstrip('/')
        model = config.get('model', 'gemini-2.0-flash-lite')
        url   = f"https://{host}/v1/gemini/v1beta/models/{model}:generateContent"
        if config.get('gemini_key'):
            headers['x-goog-api-key'] = config['gemini_key']
        # Convert messages to Gemini contents format
        contents = []
        for m in messages:
            role = 'user' if m['role'] == 'user' else 'model'
            content = m['content']
            if isinstance(content, list):
                parts = [{'text': p['text']} for p in content if p.get('type') == 'text']
            else:
                parts = [{'text': content}]
            contents.append({'role': role, 'parts': parts})
        body = {'contents': contents}
        if config.get('system'):
            body['system_instruction'] = {'parts': [{'text': config['system']}]}
        for k, v in (config.get('extra_body') or {}).items():
            if k: body[k] = v

    # ── Claude (Bedrock invoke) ──────────────────────────────────
    elif api_type == 'claude':
        host       = config.get('host', '').rstrip('/')
        cust_model = config.get('cust_model', 'cust-model')
        model_id   = config.get('model', '')
        url  = f"http://{host}/v1/{cust_model}/model/{model_id}/invoke"
        body = {
            'anthropic_version': config.get('anthropic_version', 'bedrock-2023-05-31'),
            'max_tokens': int(config.get('max_tokens') or 1024),
            'messages': messages,
        }
        if config.get('system'):    body['system']      = config['system']
        if config.get('temperature'): body['temperature'] = float(config['temperature'])
        for k, v in (config.get('extra_body') or {}).items():
            if k: body[k] = v

    # ── OpenAI ──────────────────────────────────────────────────
    else:
        host = config.get('host', '').rstrip('/')
        url  = config.get('endpoint') or f"http://{host}/v1/cust-openai/v1/responses"
        body = {'model': config.get('model', ''), 'input': messages}
        if config.get('max_tokens'):   body['max_output_tokens'] = int(config['max_tokens'])
        if config.get('temperature'):  body['temperature']       = float(config['temperature'])
        for k, v in (config.get('extra_body') or {}).items():
            if k: body[k] = v

    stream = config.get('stream', False) and api_type == 'openai'
    if stream: body['stream'] = True

    debug_request = {
        'url': url, 'method': 'POST',
        'headers': {k: ('***' if k.lower() in ('authorization','x-ns-aig-apikey','x-goog-api-key') else v)
                    for k, v in headers.items()},
        'body': body
    }

    try:
        resp = requests.post(url, headers=headers, json=body,
                             stream=stream, timeout=120, verify=ssl_verify)
        if stream:
            def generate():
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk: yield chunk
            return Response(generate(), status=resp.status_code,
                            content_type=resp.headers.get('content-type','text/event-stream'))
        try:
            body_json = resp.json()
        except Exception:
            body_json = resp.text

        return jsonify({
            'status': resp.status_code, 'body': body_json,
            'headers': dict(resp.headers),
            'debug_request': debug_request,
            'debug_response': {'status': resp.status_code, 'headers': dict(resp.headers), 'body': body_json},
            'api_type': api_type,
        })
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e), 'debug_request': debug_request, 'debug_response': None}), 500

@app.route('/api/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f    = request.files['file']
    data = f.read()
    return jsonify({'filename': f.filename, 'mime_type': f.content_type or 'application/octet-stream',
                    'base64': base64.b64encode(data).decode('utf-8'), 'size': len(data)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
