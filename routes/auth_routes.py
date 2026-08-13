"""Authentication routes: register / login / logout / forgot / admin-login."""
import os
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, make_response, current_app
from flask_login import login_user, logout_user, login_required, current_user
from flask_jwt_extended import create_access_token
from app_modules.extensions import db, limiter
from app_modules.models import User, Admin
from app_modules.security import csrf_required

auth_bp = Blueprint("auth", __name__)


def issue_jwt_response(user_dict):
    token = create_access_token(identity=user_dict["username"])
    resp = make_response(jsonify({"user": user_dict, "access_token": token}))
    resp.set_cookie("access_token_cookie", token, httponly=True, samesite="Lax")
    return resp


def _save_registration_avatar(file_storage, owner_id):
    """Save an uploaded profile image and return its relative static path, or None."""
    if not file_storage or not file_storage.filename:
        return None
    fname = secure_filename(f"{owner_id}_{file_storage.filename}")
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    file_storage.save(os.path.join(upload_dir, fname))
    return f"uploads/{fname}"


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("30 per hour")
@csrf_required
def register():
    if current_user.is_authenticated:
        return redirect(url_for("public.home"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email    = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm  = request.form.get("confirm")  or ""
        phone    = (request.form.get("phone") or "").strip()
        address  = (request.form.get("address") or "").strip()
        country  = (request.form.get("country") or "").strip()
        avatar_file = request.files.get("avatar")

        if not username or not email or len(password) < 6:
            flash("Please fill all fields. Password ≥ 6 chars.", "danger")
            return redirect(url_for("auth.register"))
        if not phone or not address or not country:
            flash("Phone number, address and country are compulsory.", "danger")
            return redirect(url_for("auth.register"))
        if not avatar_file or not avatar_file.filename:
            flash("A profile image is compulsory. Please upload one.", "danger")
            return redirect(url_for("auth.register"))
        if password != confirm:
            flash("Passwords don't match.", "danger"); return redirect(url_for("auth.register"))
        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Username or email already exists.", "warning")
            return redirect(url_for("auth.register"))
        u = User(username=username, email=email, full_name=username,
                 phone=phone, address=address, country=country)
        u.set_password(password)
        db.session.add(u); db.session.commit()
        avatar_path = _save_registration_avatar(avatar_file, u.id)
        if avatar_path:
            u.avatar = avatar_path
            db.session.commit()
        login_user(u)
        flash(f"Welcome aboard, {username}! 🎉", "success")
        return redirect(url_for("public.home"))
    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("60 per hour")
@csrf_required
def login():
    if current_user.is_authenticated:
        return redirect(url_for("public.home"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        u = User.query.filter_by(email=email).first()
        if u and u.check_password(password):
            login_user(u, remember=True)
            nxt = request.args.get("next") or url_for("public.home")
            flash("Logged in successfully.", "success"); return redirect(nxt)
        a = Admin.query.filter_by(email=email).first()
        if a and a.check_password(password):
            login_user(a, remember=True)
            flash(f"Welcome back, {a.name}.", "success")
            return redirect(url_for("admin.dashboard"))
        flash("Invalid credentials.", "danger"); return redirect(url_for("auth.login"))
    return render_template("auth/login.html", google_client_id=current_app.config.get("GOOGLE_CLIENT_ID"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user(); flash("Signed out.", "info"); return redirect(url_for("public.home"))


@auth_bp.route("/forgot", methods=["GET", "POST"])
@limiter.limit("30 per hour")
def forgot():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        u = User.query.filter_by(email=email).first()
        if u:
            # In production: send reset email by token
            flash(f"A reset link has been generated for {email} (demo).", "info")
        else:
            flash("If that email exists we sent instructions.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")


@auth_bp.route("/admin-login", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def admin_login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pwd = request.form.get("password") or ""
        a = Admin.query.filter_by(email=email).first()
        if a and a.check_password(pwd):
            from flask_login import login_user
            login_user(a, remember=True)
            session = None  # placeholder
            flash("Welcome admin.", "success"); return redirect(url_for("admin.dashboard"))
        flash("Invalid admin credentials.", "danger")
    return render_template("auth/admin_login.html")


@auth_bp.route("/google-login", methods=["POST"])
@limiter.limit("30 per hour")
def google_login():
    """Verify a real Google Identity Services ID token and log the user in."""
    import re
    import secrets as _secrets

    payload = request.get_json(silent=True) or {}
    token = payload.get("credential")
    if not token:
        return jsonify({"error": "missing credential"}), 400

    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    if not client_id:
        return jsonify({"error": "Google sign-in is not configured on this server."}), 501

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        info = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), client_id
        )
        if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            raise ValueError("Wrong issuer.")
    except Exception as ex:
        current_app.logger.warning("Google token verification failed: %s", ex)
        return jsonify({"error": "Invalid or expired Google token."}), 401

    if not info.get("email_verified", False):
        return jsonify({"error": "Google account email is not verified."}), 401

    email = (info.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Google account has no email."}), 400

    u = User.query.filter_by(email=email).first()
    if not u:
        base_username = re.sub(r"[^a-zA-Z0-9_]", "", (info.get("given_name") or email.split("@")[0])).lower() or "user"
        username = base_username
        suffix = 1
        while User.query.filter_by(username=username).first():
            suffix += 1
            username = f"{base_username}{suffix}"
        u = User(
            username=username,
            email=email,
            full_name=info.get("name") or username,
            email_verified=True,
            avatar=info.get("picture") or "images/default-avatar.png",
        )
        u.set_password(_secrets.token_urlsafe(24))  # random unusable password; login stays Google-only
        db.session.add(u)
        db.session.commit()
    elif not u.email_verified:
        u.email_verified = True
        db.session.commit()

    login_user(u, remember=True)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes["application/json"]:
        return issue_jwt_response(u.to_dict())
    return redirect(url_for("public.home"))
