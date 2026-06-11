from flask import Flask, request, jsonify, render_template, Response
import requests
import json
import base64
import os

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    config = data.get('config', {})
    messages = data.get('messages', [])
    stream = data.get('stream', False)

    endpoint = config.get('endpoint', '')
    bearer_token = config.get('bearer_token', '')
    api_key = config.get('api_key', '')
    model = config.get('model', '')
    extra_headers = config.get('extra_headers', {})
    extra_body = config.get('extra_body', {})

    headers = {
        'Content-Type': 'application/json',
    }
    if bearer_token:
        headers['Authorization'] = f'Bearer {bearer_token}'
    if api_key:
        headers['x-ns-aig-apikey'] = api_key
    for k, v in extra_headers.items():
        if k and v:
            headers[k] = v

    body = {
        'model': model,
        'input': messages,
        **extra_body
    }

    if stream:
        body['stream'] = True

    try:
        resp = requests.post(
            endpoint,
            headers=headers,
            json=body,
            stream=stream,
            timeout=120,
            verify=False
        )

        if stream:
            def generate():
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
            return Response(generate(), status=resp.status_code,
                          content_type=resp.headers.get('content-type', 'text/event-stream'))
        else:
            return jsonify({
                'status': resp.status_code,
                'body': resp.json() if resp.headers.get('content-type','').startswith('application/json') else resp.text,
                'headers': dict(resp.headers)
            })
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    data = f.read()
    b64 = base64.b64encode(data).decode('utf-8')
    mime = f.content_type or 'application/octet-stream'
    return jsonify({
        'filename': f.filename,
        'mime_type': mime,
        'base64': b64,
        'size': len(data)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
