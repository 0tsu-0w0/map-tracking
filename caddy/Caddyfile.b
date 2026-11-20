{
	email {$ACME_EMAIL}
}

# MQTT over WebSocket(OwnTracks アプリの接続先)。案A と同じ
{$MQTT_DOMAIN} {
	reverse_proxy mosquitto:9001
}

# 自作の地図画面と API。ログインは API 側(Cookie セッション)で行う
{$APP_DOMAIN} {
	encode zstd gzip
	header {
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
