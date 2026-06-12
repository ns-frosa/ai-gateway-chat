from flask import Flask, request, jsonify, render_template, Response
import requests
import json
import base64
import re
import os
import glob

app = Flask(__name__)
PROFILES_DIR = os.path.join(os.path.dirname(__file__), 'profiles')

# ── Template substitution ────────────────────────────────────────────
def _sub(tpl, vars):
    """Recursively substitute {key} placeholders in a template (str/dict/list).
    Unresolved placeholders become ''. Bare numeric placeholders keep their type."""
    if isinstance(tpl, str):
        # Preserve numeric type for bare placeholders like "{max_tokens}"
        m = re.fullmatch(r'\{(\w+)\}', tpl)
        if m:
            val = vars.get(m.group(1), '')
            return val  # may be int/float/str
        return re.sub(r'\{(\w+)\}', lambda m: str(vars.get(m.group(1), '')), tpl)
    if isinstance(tpl, dict):
        return {k: _sub(v, vars) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [_sub(i, vars) for i in tpl]
    return tpl

# ── Generic provider engine ──────────────────────────────────────────
def _build_provider_request(provider, messages, config):
    """Build (url, body, extra_headers) from a provider-block profile.

    Provider block schema:
      url          – URL template with {host}, {model}, {cust_model}, …
      messages_key – top-level body key for the message array  (default: "messages")
      content_key  – key inside each message for parts array   (default: "content")
      role_map     – dict remapping role names, e.g. {"assistant": "model"}
      text_part    – template object for a text content part
      image_part   – template object for an image content part
                     Variables: {data_url}, {mime_type}, {base64}
      extra_body   – dict merged into body before user overrides (supports templates)
      extra_headers– dict merged into request headers           (supports templates)
      system       – null | {"field": "key"} | {"field": "key", "template": {...}}
                     | {"as_message": true, "role": "system"}
      response_path– dot-path string or list of paths to extract reply text
                     Syntax: "key[n]", "key[*]", "key[k=v]"
    """
    vars = {
        'host':              config.get('host', '').rstrip('/'),
        'model':             config.get('model', ''),
        'cust_model':        config.get('cust_model', 'cust-model'),
        'anthropic_version': config.get('anthropic_version', 'bedrock-2023-05-31'),
        'system':            config.get('system', ''),
        'gemini_key':        config.get('gemini_key', ''),
    }
    if config.get('max_tokens'):
        vars['max_tokens'] = int(config['max_tokens'])

    url          = config.get('endpoint') or _sub(provider.get('url', ''), vars)
    role_map     = provider.get('role_map', {})
    content_key  = provider.get('content_key', 'content')
    messages_key = provider.get('messages_key', 'messages')
    text_tpl     = provider.get('text_part', {'type': 'text', 'text': '{text}'})
    image_tpl    = provider.get('image_part', {})

    # Convert messages to provider format
    converted = []
    for m in messages:
        role    = role_map.get(m['role'], m['role'])
        content = m['content']
        if isinstance(content, list):
            parts = []
            for p in content:
                if p.get('type') == 'text':
                    parts.append(_sub(text_tpl, {**vars, 'text': p['text']}))
                elif p.get('type') == 'image_url' and image_tpl:
                    raw_url  = p['image_url']['url']
                    hdr, b64 = raw_url.split(',', 1)
                    mime     = hdr.split(':')[1].split(';')[0]
                    parts.append(_sub(image_tpl, {**vars, 'data_url': raw_url,
                                                  'mime_type': mime, 'base64': b64}))
        else:
            parts = [_sub(text_tpl, {**vars, 'text': content})]
        converted.append({'role': role, content_key: parts})

    # Build body: provider defaults first
    body = {messages_key: converted}
    for k, v in _sub(provider.get('extra_body', {}), vars).items():
        if k and v != '' and v is not None:
            body[k] = v

    # User-level max_tokens / temperature override provider defaults
    if config.get('max_tokens'):
        body['max_tokens'] = int(config['max_tokens'])
    if config.get('temperature'):
        body['temperature'] = float(config['temperature'])

    # User extra_body (highest precedence)
    for k, v in (config.get('extra_body') or {}).items():
        if k: body[k] = v

    # System prompt
    sys_cfg = provider.get('system')
    if sys_cfg and config.get('system'):
        field = sys_cfg.get('field')
        tpl   = sys_cfg.get('template')
        if field:
            body[field] = _sub(tpl, {**vars, 'value': config['system']}) if tpl else config['system']
        elif sys_cfg.get('as_message'):
            sys_role  = sys_cfg.get('role', 'system')
            sys_parts = [_sub(text_tpl, {**vars, 'text': config['system']})]
            converted.insert(0, {'role': sys_role, content_key: sys_parts})

    # Provider-level extra headers (e.g. x-goog-api-key for Gemini)
    extra_hdrs = {k: v for k, v in _sub(provider.get('extra_headers', {}), vars).items()
                  if k and v != ''}

    return url, body, extra_hdrs


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

    headers = {'Content-Type': 'application/json'}
    if config.get('bearer_token'):
        headers['Authorization'] = f"Bearer {config['bearer_token']}"
    if config.get('api_key'):
        headers['x-ns-aig-apikey'] = config['api_key']
    for k, v in (config.get('extra_headers') or {}).items():
        if k and v: headers[k] = v

    ssl_verify = config.get('ssl_verify', False)
    provider   = config.get('provider')

    if provider:
        # ── Generic provider engine ──────────────────────────────
        url, body, extra_hdrs = _build_provider_request(provider, messages, config)
        headers.update(extra_hdrs)
        api_type = config.get('api_type', 'custom')
        stream   = False

    else:
        # ── Legacy api_type handling (backward compat) ───────────
        api_type = config.get('api_type', 'openai')

        def _parse_data_url(url_str):
            header, b64data = url_str.split(',', 1)
            mime = header.split(':')[1].split(';')[0]
            return mime, b64data

        def _to_gemini_parts(content):
            if not isinstance(content, list):
                return [{'text': content}]
            parts = []
            for p in content:
                if p.get('type') == 'text':
                    parts.append({'text': p['text']})
                elif p.get('type') == 'image_url':
                    mime, b64 = _parse_data_url(p['image_url']['url'])
                    parts.append({'inlineData': {'mimeType': mime, 'data': b64}})
            return parts

        def _to_claude_content(content):
            if not isinstance(content, list):
                return content
            parts = []
            for p in content:
                if p.get('type') == 'text':
                    parts.append({'type': 'text', 'text': p['text']})
                elif p.get('type') == 'image_url':
                    mime, b64 = _parse_data_url(p['image_url']['url'])
                    parts.append({'type': 'image', 'source': {'type': 'base64',
                                  'media_type': mime, 'data': b64}})
            return parts

        def _to_openai_content(content):
            if not isinstance(content, list):
                return content
            parts = []
            for p in content:
                if p.get('type') == 'text':
                    parts.append({'type': 'input_text', 'text': p['text']})
                elif p.get('type') == 'image_url':
                    parts.append({'type': 'input_image', 'image_url': p['image_url']['url']})
            return parts

        if api_type == 'gemini':
            host  = config.get('host', '').rstrip('/')
            model = config.get('model', 'gemini-2.0-flash-lite')
            url   = f"https://{host}/v1/gemini/v1beta/models/{model}:generateContent"
            if config.get('gemini_key'):
                headers['x-goog-api-key'] = config['gemini_key']
            contents = []
            for m in messages:
                role = 'user' if m['role'] == 'user' else 'model'
                contents.append({'role': role, 'parts': _to_gemini_parts(m['content'])})
            body = {'contents': contents}
            if config.get('system'):
                body['system_instruction'] = {'parts': [{'text': config['system']}]}
            for k, v in (config.get('extra_body') or {}).items():
                if k: body[k] = v

        elif api_type == 'claude':
            host       = config.get('host', '').rstrip('/')
            cust_model = config.get('cust_model', 'cust-model')
            model_id   = config.get('model', '')
            url  = f"http://{host}/v1/{cust_model}/model/{model_id}/invoke"
            claude_messages = [{'role': m['role'], 'content': _to_claude_content(m['content'])}
                               for m in messages]
            body = {
                'anthropic_version': config.get('anthropic_version', 'bedrock-2023-05-31'),
                'max_tokens': int(config.get('max_tokens') or 1024),
                'messages': claude_messages,
            }
            if config.get('system'):      body['system']      = config['system']
            if config.get('temperature'): body['temperature'] = float(config['temperature'])
            for k, v in (config.get('extra_body') or {}).items():
                if k: body[k] = v

        else:  # openai
            host = config.get('host', '').rstrip('/')
            url  = config.get('endpoint') or f"http://{host}/v1/cust-openai/v1/responses"
            openai_messages = [{'role': m['role'], 'content': _to_openai_content(m['content'])}
                               for m in messages]
            body = {'model': config.get('model', ''), 'input': openai_messages}
            if config.get('max_tokens'):   body['max_output_tokens'] = int(config['max_tokens'])
            if config.get('temperature'):  body['temperature']       = float(config['temperature'])
            for k, v in (config.get('extra_body') or {}).items():
                if k: body[k] = v

        stream = config.get('stream', False) and api_type == 'openai'
        if stream: body['stream'] = True

    debug_request = {
        'url': url, 'method': 'POST',
        'headers': {k: ('***' if k.lower() in ('authorization', 'x-ns-aig-apikey', 'x-goog-api-key') else v)
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
                            content_type=resp.headers.get('content-type', 'text/event-stream'))
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
