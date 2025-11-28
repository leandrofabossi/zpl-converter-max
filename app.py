from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import os
import time
import io
import re
import zipfile
import requests
import mercadopago
from pypdf import PdfWriter
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "zpl_max_money_pro"

# --- MERCADO PAGO CONFIG ---
# Seu Token já inserido aqui:
sdk = mercadopago.SDK("APP_USR-e97cc02f-0008-40aa-8339-d5e6d3ff6f4c")

# --- BANCO DE DADOS (Híbrido: Nuvem/Local) ---
database_url = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- MODELO DO USUÁRIO ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    is_paid = db.Column(db.Boolean, default=False) # Controle de pagamento

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- CONFIGURAÇÕES GERAIS ---
UPLOAD_FOLDER = 'uploads'
DOWNLOAD_FOLDER = 'downloads'
os
