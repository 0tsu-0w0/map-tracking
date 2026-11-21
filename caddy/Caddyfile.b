{
	email {$ACME_EMAIL}
	# お試し(--local)のときは local_certs が入り、自己署名の証明書を使う
	{$CADDY_GLOBAL_OPTIONS}
}

# MQTT over WebSocket(OwnTracks アプリの接続先)。案A と同じ
{$MQTT_DOMAIN} {
	reverse_proxy mosquitto:9001
}

# 自作の地図画面と API。ログインは API 側(Cookie セッション)で行う
{$APP_DOMAIN} {
	encode zstd gzip
	# 位置の送信や API への入力は小さい。大きな本文は受け付けない
	request_body {
		max_size 1MB
	}
	header {
		# 地図画面が読むのは自分のサイトと地図タイル(https)だけ。MapLibre は worker を blob: で動かすことがある
		Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; connect-src 'self' https: wss://{$APP_DOMAIN}; worker-src 'self' blob:; child-src 'self' blob:; font-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
		Permissions-Policy "geolocation=(), camera=(), microphone=()"
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "same-origin"
		-Server
	}
	# /healthz は外に見せない(Docker のヘルスチェック用)
	respond /healthz 404
	reverse_proxy api:8000
}
