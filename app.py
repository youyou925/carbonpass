"""
CarbonPass - 碳排放核算与合规报告平台
基于欧盟 CBAM 规范的马来西亚中小制造企业碳管理 SaaS
"""

import os
import uuid
import math
import json
import datetime
from datetime import timedelta, date
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

# 加载 .env 文件（优先于系统环境变量）
from dotenv import load_dotenv
load_dotenv(override=True)

# 本地开发允许 HTTP（非 HTTPS）走 OAuth
os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')

from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import bcrypt
import email_validator

from flask import (
    Flask, request, render_template, redirect, url_for,
    send_from_directory, jsonify, session, flash, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required
)
from flask_mail import Mail, Message
from flask_dance.contrib.google import make_google_blueprint, google

# ============================================================
# APP INIT
# ============================================================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'carbon-pass-secret-key-2024')

# Google OAuth 凭证检查（模块级常量，供 context_processor 使用）
_GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
_GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_OAUTH_ENABLED = bool(
    _GOOGLE_CLIENT_ID
    and _GOOGLE_CLIENT_ID not in ('', 'your-client-id-here')
    and _GOOGLE_CLIENT_SECRET
    and _GOOGLE_CLIENT_SECRET not in ('', 'your-client-secret-here')
)

# Database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///carbonpass.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Mail (console only for MVP)
app.config['MAIL_SUPPRESS_SEND'] = True
app.config['MAIL_DEFAULT_SENDER'] = 'noreply@carbonpass.com'
mail = Mail(app)

# Upload folders
UPLOAD_FOLDER = 'uploads'
REPORTS_FOLDER = 'reports'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['REPORTS_FOLDER'] = REPORTS_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)


# ============================================================
# CONSTANTS (per PRD Section 6 & 9)
# ============================================================
MALAYSIA_STATES = [
    'Johor', 'Kedah', 'Kelantan', 'Melaka', 'Negeri Sembilan',
    'Pahang', 'Perak', 'Perlis', 'Penang', 'Selangor', 'Terengganu',
    'Sabah', 'Sarawak', 'Kuala Lumpur', 'Putrajaya', 'Labuan'
]

STATE_TO_REGION = {
    'Johor': 'peninsular', 'Kedah': 'peninsular', 'Kelantan': 'peninsular',
    'Melaka': 'peninsular', 'Negeri Sembilan': 'peninsular', 'Pahang': 'peninsular',
    'Perak': 'peninsular', 'Perlis': 'peninsular', 'Penang': 'peninsular',
    'Selangor': 'peninsular', 'Terengganu': 'peninsular',
    'Kuala Lumpur': 'peninsular', 'Putrajaya': 'peninsular', 'Labuan': 'peninsular',
    'Sabah': 'sabah', 'Sarawak': 'sarawak'
}

STATE_GRID_FACTORS = {
    'peninsular': 0.571,  # kg CO₂/kWh
    'sabah': 0.758,
    'sarawak': 0.234
}

CBAM_INDUSTRIES = [
    'iron_steel', 'aluminium', 'cement', 'fertilisers',
    'other_metal', 'other'
]

INDUSTRY_LABELS = {
    'zh': {
        'iron_steel': '钢铁制造（Iron & Steel）',
        'aluminium': '铝制品制造（Aluminium Products）',
        'cement': '水泥制造（Cement）',
        'fertilisers': '化肥制造（Fertilisers）',
        'other_metal': '其他金属制品（Other Metal Products）',
        'other': '其他（Other Manufacturing）'
    },
    'en': {
        'iron_steel': 'Iron & Steel',
        'aluminium': 'Aluminium Products',
        'cement': 'Cement',
        'fertilisers': 'Fertilisers',
        'other_metal': 'Other Metal Products',
        'other': 'Other Manufacturing'
    }
}

EXPORT_MARKETS = ['EU', 'Japan', 'Korea', 'China', 'Other']

# Emission factors per PRD Section 6
GRID_FACTORS = {
    'peninsular': {'factor': 0.571, 'unit': 'kg CO₂/kWh', 'source': 'Suruhanjaya Tenaga Malaysia 2022'},
    'sabah': {'factor': 0.758, 'unit': 'kg CO₂/kWh', 'source': 'Suruhanjaya Tenaga Malaysia (SESB Grid) 2022'},
    'sarawak': {'factor': 0.234, 'unit': 'kg CO₂/kWh', 'source': 'Suruhanjaya Tenaga Malaysia (SESCO Grid) 2022'}
}

FUEL_FACTORS = {
    'diesel': {'factor': 2.68, 'unit': 'kg CO₂e/L', 'source': 'IPCC AR6'},
    'petrol': {'factor': 2.31, 'unit': 'kg CO₂e/L', 'source': 'IPCC AR6'},
    'natural_gas': {'factor': 2.00, 'unit': 'kg CO₂e/m³', 'source': 'IPCC AR6'},
    'lpg': {'factor': 2.98, 'unit': 'kg CO₂e/kg', 'source': 'IPCC AR6'},
    'coal': {'factor': 2.42, 'unit': 'kg CO₂e/kg', 'source': 'IPCC AR6'},
    'coke': {'factor': 3.17, 'unit': 'kg CO₂e/kg', 'source': 'IPCC AR6'},
    'fuel_oil': {'factor': 3.17, 'unit': 'kg CO₂e/kg', 'source': 'IPCC AR6'}
}

CBAM_DEFAULT_INTENSITIES = {
    'steel_bof': {'factor': 2.15, 'unit': 'tonne CO₂e/tonne', 'source': 'EU CBAM 2025'},
    'steel_eaf': {'factor': 0.44, 'unit': 'tonne CO₂e/tonne', 'source': 'EU CBAM 2025'},
    'aluminium_primary': {'factor': 8.38, 'unit': 'tonne CO₂e/tonne', 'source': 'EU CBAM 2025'},
    'aluminium_secondary': {'factor': 0.93, 'unit': 'tonne CO₂e/tonne', 'source': 'EU CBAM 2025'},
    'cement_clinker': {'factor': 0.83, 'unit': 'tonne CO₂e/tonne', 'source': 'EU CBAM 2025'},
    'ammonia': {'factor': 1.98, 'unit': 'tonne CO₂e/tonne', 'source': 'EU CBAM 2025'}
}

# ============================================================
# LANGUAGE SUPPORT
# ============================================================
LANGUAGES = ['zh', 'en']

TRANSLATIONS = {
    'dashboard': {'zh': '仪表盘', 'en': 'Dashboard'},
    'data_entry': {'zh': '数据填报', 'en': 'Data Entry'},
    'results': {'zh': '核算结果', 'en': 'Results'},
    'reports': {'zh': '报告管理', 'en': 'Reports'},
    'settings': {'zh': '企业设置', 'en': 'Settings'},
    'admin': {'zh': '管理后台', 'en': 'Admin'},
    'logout': {'zh': '退出登录', 'en': 'Logout'},
    'login': {'zh': '登录', 'en': 'Login'},
    'register': {'zh': '注册', 'en': 'Register'},
    'save': {'zh': '保存', 'en': 'Save'},
    'cancel': {'zh': '取消', 'en': 'Cancel'},
    'submit': {'zh': '提交', 'en': 'Submit'},
    'download': {'zh': '下载', 'en': 'Download'},
    'delete': {'zh': '删除', 'en': 'Delete'},
    'edit': {'zh': '编辑', 'en': 'Edit'},
    'back': {'zh': '返回', 'en': 'Back'},
    'next': {'zh': '下一步', 'en': 'Next'},
    'prev': {'zh': '上一步', 'en': 'Previous'},
}

def get_lang():
    lang = request.args.get('lang')
    if lang in LANGUAGES:
        session['lang'] = lang
        return lang
    if 'lang' in session and session['lang'] in LANGUAGES:
        return session['lang']
    return 'zh'

def _(key):
    lang = get_lang()
    t = TRANSLATIONS.get(key, {})
    return t.get(lang, key)

# ============================================================
# DATABASE MODELS
# ============================================================

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=False)
    position = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    avatar = db.Column(db.String(500))
    role = db.Column(db.String(20), default='user')  # user | system_admin
    email_verified = db.Column(db.Boolean, default=False)
    login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    def __repr__(self):
        return f'<User {self.email}>'

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(
            password.encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')

    def check_password(self, password):
        if not self.password_hash:
            return False
        return bcrypt.checkpw(
            password.encode('utf-8'), self.password_hash.encode('utf-8')
        )

    def is_locked(self):
        if self.locked_until and self.locked_until > datetime.datetime.utcnow():
            return True
        return False


class Company(db.Model):
    __tablename__ = 'companies'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    name_en = db.Column(db.String(255))
    ssm_number = db.Column(db.String(50))
    state = db.Column(db.String(50))
    address = db.Column(db.Text)
    industry = db.Column(db.String(50))
    export_markets = db.Column(db.String(255))  # comma-separated
    website = db.Column(db.String(255))
    contact_phone = db.Column(db.String(50))
    logo_url = db.Column(db.String(500))
    product_name = db.Column(db.String(255))
    product_unit = db.Column(db.String(20), default='tonnes')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    user = db.relationship('User', backref=db.backref('company', uselist=False))

    def get_export_markets_list(self):
        if self.export_markets:
            return [m.strip() for m in self.export_markets.split(',')]
        return []

    def get_grid_region(self):
        if self.state and self.state in STATE_TO_REGION:
            return STATE_TO_REGION[self.state]
        return 'peninsular'

    def get_grid_factor(self):
        region = self.get_grid_region()
        return GRID_FACTORS[region]


class ReportingPeriod(db.Model):
    __tablename__ = 'reporting_periods'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    quarter = db.Column(db.Integer, nullable=False)  # 1-4
    status = db.Column(db.String(20), default='draft')  # draft | submitted | calculated
    confirmation = db.Column(db.Boolean, default=False)
    other_emissions = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref='periods')
    company = db.relationship('Company', backref='periods')
    energy_inputs = db.relationship('EnergyInput', uselist=False, backref='period')
    production_inputs = db.relationship('ProductionInput', backref='period', lazy='dynamic')
    calculation_result = db.relationship('CalculationResult', uselist=False, backref='period')
    reports = db.relationship('Report', backref='period', lazy='dynamic')

    def label(self, lang='zh'):
        if lang == 'en':
            return f"{self.year} Q{self.quarter}"
        return f"{self.year}年第{self.quarter}季度"

    def quarter_months(self):
        return {
            1: ('Jan', 'Mar'), 2: ('Apr', 'Jun'),
            3: ('Jul', 'Sep'), 4: ('Oct', 'Dec')
        }.get(self.quarter, ('', ''))


class EnergyInput(db.Model):
    __tablename__ = 'energy_inputs'
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey('reporting_periods.id'), unique=True, nullable=False)
    electricity_kwh = db.Column(db.Float, default=0)
    diesel_litres = db.Column(db.Float, default=0)
    petrol_litres = db.Column(db.Float, default=0)
    natural_gas_m3 = db.Column(db.Float, default=0)
    lpg_kg = db.Column(db.Float, default=0)
    coal_tonne = db.Column(db.Float, default=0)
    other_fuel_label = db.Column(db.String(100))
    other_fuel_amount = db.Column(db.Float, default=0)
    other_fuel_unit = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class ProductionInput(db.Model):
    __tablename__ = 'production_inputs'
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey('reporting_periods.id'), nullable=False)
    product_name = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='tonnes')
    export_to_eu = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def amount_in_tonnes(self):
        """Convert to tonnes for allocation calculation"""
        if self.unit == 'tonnes':
            return self.amount
        elif self.unit == 'kg':
            return self.amount / 1000
        elif self.unit == 'pieces':
            return self.amount  # can't convert, use as-is
        elif self.unit == 'm3':
            return self.amount
        return self.amount


class CalculationResult(db.Model):
    __tablename__ = 'calculation_results'
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey('reporting_periods.id'), unique=True, nullable=False)
    scope1_tonnes = db.Column(db.Float, default=0)
    scope2_tonnes = db.Column(db.Float, default=0)
    total_tonnes = db.Column(db.Float, default=0)
    methodology = db.Column(db.String(100), default='GHG Protocol + CBAM')
    grid_factor_used = db.Column(db.Float)
    grid_factor_source = db.Column(db.String(255))
    uncertainty_pct = db.Column(db.Float, default=0)
    data_quality = db.Column(db.String(20), default='Measured')
    calculation_details = db.Column(db.Text)  # JSON
    emission_factors_snapshot = db.Column(db.Text)  # JSON
    calculated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    product_intensities = db.relationship('ProductEmissionIntensity', backref='result', lazy='dynamic')


class ProductEmissionIntensity(db.Model):
    __tablename__ = 'product_emission_intensities'
    id = db.Column(db.Integer, primary_key=True)
    result_id = db.Column(db.Integer, db.ForeignKey('calculation_results.id'), nullable=False)
    product_name = db.Column(db.String(255))
    amount = db.Column(db.Float)
    unit = db.Column(db.String(20))
    attributed_emissions = db.Column(db.Float)  # tonnes CO₂e
    intensity = db.Column(db.Float)  # tonnes CO₂e/unit
    export_to_eu = db.Column(db.Boolean, default=False)


class EmissionFactor(db.Model):
    __tablename__ = 'emission_factors'
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)  # fuel | electricity | cbam_default
    subcategory = db.Column(db.String(50), nullable=False)
    factor = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(50), nullable=False)
    source = db.Column(db.String(255))
    year = db.Column(db.Integer)
    region = db.Column(db.String(50), default='national')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class Report(db.Model):
    __tablename__ = 'reports'
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey('reporting_periods.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type = db.Column(db.String(30), default='cbam')  # cbam | ghg_summary
    language = db.Column(db.String(10), default='en')
    filename = db.Column(db.String(255))  # UUID-based safe filename
    display_name = db.Column(db.String(255))
    file_size = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    user = db.relationship('User', backref='reports')

# ============================================================
# TOKEN / PASSWORD RESET
# ============================================================
serializer = URLSafeTimedSerializer(app.secret_key)

def generate_reset_token(user_id, expires_sec=3600):
    return serializer.dumps(user_id, salt='password-reset')

def verify_reset_token(token, expires_sec=3600):
    try:
        user_id = serializer.loads(token, salt='password-reset', max_age=expires_sec)
    except (SignatureExpired, BadSignature):
        return None
    return User.query.get(user_id)

# ============================================================
# LOGIN MANAGER
# ============================================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================================
# DECORATORS
# ============================================================
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role != 'system_admin':
            flash('Admin access required', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ============================================================
# CONTEXT PROCESSORS
# ============================================================
@app.context_processor
def inject_globals():
    return {
        'lang': get_lang(),
        'languages': LANGUAGES,
        'current_user': current_user,
        'states': MALAYSIA_STATES,
        'industries': CBAM_INDUSTRIES,
        'industry_labels': INDUSTRY_LABELS.get(get_lang(), INDUSTRY_LABELS['zh']),
        'export_markets': EXPORT_MARKETS,
        '_': _,
        'now': datetime.datetime.now(),
        'google_enabled': GOOGLE_OAUTH_ENABLED,
    }

# ============================================================
# LANG ROUTE HELPER
# ============================================================
def lang_redirect(endpoint, **kwargs):
    return redirect(url_for(endpoint, lang=get_lang(), **kwargs))

# ============================================================
# GOOGLE OAUTH
# ============================================================
google_bp = make_google_blueprint(
    client_id=_GOOGLE_CLIENT_ID or 'placeholder',
    client_secret=_GOOGLE_CLIENT_SECRET or 'placeholder',
    scope=['openid', 'email', 'profile'],
    redirect_to='login_google_callback'
)
app.register_blueprint(google_bp, url_prefix='/login/google')

# ============================================================
# CALCULATION ENGINE (per PRD Section 7)
# ============================================================

def calculate_emissions(period):
    """Calculate emissions for a reporting period per PRD Section 7."""
    energy = period.energy_inputs
    products = list(period.production_inputs)
    company = period.company

    if not energy:
        return None

    grid_info = company.get_grid_factor() if company else GRID_FACTORS['peninsular']
    grid_factor = grid_info['factor']
    grid_source = grid_info['source']

    # Step 1 & 2: Calculate Scope 1 (fuel combustion) and Scope 2 (electricity)
    scope1_details = []
    scope2_details = []

    fuels = [
        ('diesel', energy.diesel_litres, 'L', FUEL_FACTORS['diesel']),
        ('petrol', energy.petrol_litres, 'L', FUEL_FACTORS['petrol']),
        ('natural_gas', energy.natural_gas_m3, 'm³', FUEL_FACTORS['natural_gas']),
        ('lpg', energy.lpg_kg, 'kg', FUEL_FACTORS['lpg']),
    ]
    # Coal: stored in tonnes, factor is per kg
    if energy.coal_tonne > 0:
        factor = FUEL_FACTORS['coal']
        emission_kg = energy.coal_tonne * 1000 * factor['factor']
        scope1_details.append({
            'name': 'coal', 'label': '煤炭 (Coal)',
            'amount': energy.coal_tonne, 'unit': 'tonne',
            'factor': factor['factor'], 'factor_unit': factor['unit'],
            'factor_source': factor['source'],
            'emissions_tonne': round(emission_kg / 1000, 6)
        })

    for fuel_key, amount, unit, factor_info in fuels:
        if amount > 0:
            emission_kg = amount * factor_info['factor']
            label_map = {
                'diesel': '柴油 (Diesel)', 'petrol': '汽油 (Petrol)',
                'natural_gas': '天然气 (Natural Gas)', 'lpg': '液化石油气 (LPG)'
            }
            scope1_details.append({
                'name': fuel_key,
                'label': label_map.get(fuel_key, fuel_key),
                'amount': amount, 'unit': unit,
                'factor': factor_info['factor'],
                'factor_unit': factor_info['unit'],
                'factor_source': factor_info['source'],
                'emissions_tonne': round(emission_kg / 1000, 6)
            })

    # Other fuel
    if energy.other_fuel_amount and energy.other_fuel_amount > 0:
        scope1_details.append({
            'name': 'other',
            'label': energy.other_fuel_label or '其他燃料 (Other)',
            'amount': energy.other_fuel_amount,
            'unit': energy.other_fuel_unit or 'kg',
            'factor': 0, 'factor_unit': '',
            'factor_source': 'User defined',
            'emissions_tonne': energy.other_fuel_amount
        })

    # Electricity (Scope 2)
    if energy.electricity_kwh > 0:
        emission_kg = energy.electricity_kwh * grid_factor
        scope2_details.append({
            'name': 'electricity',
            'label': '外购电力 (Grid Electricity)',
            'amount': energy.electricity_kwh, 'unit': 'kWh',
            'factor': grid_factor,
            'factor_unit': 'kg CO₂/kWh',
            'factor_source': grid_source,
            'emissions_tonne': round(emission_kg / 1000, 6)
        })

    # Step 3: Aggregate
    scope1_total = sum(d['emissions_tonne'] for d in scope1_details)
    scope2_total = sum(d['emissions_tonne'] for d in scope2_details)

    # Other emissions (from step 3 of wizard)
    other_tonnes = period.other_emissions or 0

    total_emissions = scope1_total + scope2_total + other_tonnes

    # Round intermediate
    scope1_total = round(scope1_total, 6)
    scope2_total = round(scope2_total, 6)
    total_emissions = round(total_emissions, 6)

    # Step 4: Product emission intensity (proportional allocation)
    product_results = []
    total_production = sum(p.amount_in_tonnes() for p in products)

    for p in products:
        prod_amount = p.amount_in_tonnes()
        if total_production > 0:
            attributed = total_emissions * (prod_amount / total_production)
            intensity = attributed / prod_amount if prod_amount > 0 else 0
        else:
            attributed = total_emissions / len(products) if products else 0
            intensity = attributed / prod_amount if prod_amount > 0 else 0

        product_results.append({
            'product_name': p.product_name,
            'amount': p.amount,
            'unit': p.unit,
            'attributed_emissions': round(attributed, 4),
            'intensity': round(intensity, 4),
            'export_to_eu': p.export_to_eu
        })

    # Uncertainty (per PRD Section 7)
    uncertainty_components = []
    if energy.electricity_kwh > 0:
        uncertainty_components.append(5)
    if energy.diesel_litres > 0 or energy.natural_gas_m3 > 0 or energy.coal_tonne > 0:
        uncertainty_components.append(3)
    if products:
        uncertainty_components.append(2)
    uncertainty_components.append(10)
    total_uncertainty = round(math.sqrt(sum(u**2 for u in uncertainty_components)), 1)

    # Build calculation details JSON
    calc_details = {
        'scope1_details': scope1_details,
        'scope2_details': scope2_details,
        'other_emissions_tonnes': other_tonnes,
        'formula_scope1': 'Σ(燃料消耗量 × 排放因子) / 1000',
        'formula_scope2': '电力消耗量(kWh) × 电网因子(kgCO₂/kWh) / 1000',
        'formula_allocation': '产品归因排放 = 总排放 × (产品产量 / 总产量)'
    }

    # Emission factors snapshot
    factors_snapshot = []
    for d in scope1_details:
        factors_snapshot.append({
            'type': d['name'], 'value': d['factor'],
            'unit': d['factor_unit'], 'source': d['factor_source']
        })
    for d in scope2_details:
        factors_snapshot.append({
            'type': d['name'], 'value': d['factor'],
            'unit': d['factor_unit'], 'source': d['factor_source']
        })

    # Save to DB
    calc = CalculationResult.query.filter_by(period_id=period.id).first()
    if not calc:
        calc = CalculationResult(period_id=period.id)
        db.session.add(calc)

    calc.scope1_tonnes = round(scope1_total, 2)
    calc.scope2_tonnes = round(scope2_total, 2)
    calc.total_tonnes = round(total_emissions, 2)
    calc.methodology = 'GHG Protocol Corporate Standard + EU CBAM Regulation (EU) 2023/956'
    calc.grid_factor_used = grid_factor
    calc.grid_factor_source = grid_source
    calc.uncertainty_pct = total_uncertainty
    calc.data_quality = 'Measured'
    calc.calculation_details = json.dumps(calc_details, ensure_ascii=False)
    calc.emission_factors_snapshot = json.dumps(factors_snapshot, ensure_ascii=False)
    calc.calculated_at = datetime.datetime.utcnow()

    # Save product intensities
    ProductEmissionIntensity.query.filter_by(result_id=calc.id).delete()
    for pr in product_results:
        pei = ProductEmissionIntensity(
            result_id=calc.id,
            product_name=pr['product_name'],
            amount=pr['amount'],
            unit=pr['unit'],
            attributed_emissions=pr['attributed_emissions'],
            intensity=pr['intensity'],
            export_to_eu=pr['export_to_eu']
        )
        db.session.add(pei)

    period.status = 'calculated'
    period.submitted_at = datetime.datetime.utcnow()
    db.session.commit()

    return calc

# ============================================================
# PDF REPORT GENERATOR (per PRD Section 8)
# ============================================================

def generate_cbam_pdf(period_id):
    """Generate full CBAM PDF report."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm, cm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image as RLImage
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

    period = ReportingPeriod.query.get(period_id)
    if not period:
        return None

    calc = period.calculation_result
    company = period.company
    energy = period.energy_inputs
    products = list(period.production_inputs)

    # Safe filename (UUID)
    safe_filename = f"{uuid.uuid4().hex}.pdf"
    filepath = os.path.join(REPORTS_FOLDER, safe_filename)

    # Display name
    company_name_safe = company.name.replace(' ', '_') if company else 'Unknown'
    display_name = f"CarbonPass_{company_name_safe}_CBAM_Report_{period.year}_Q{period.quarter}.pdf"

    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        topMargin=20*mm, bottomMargin=20*mm,
        leftMargin=25*mm, rightMargin=25*mm
    )

    styles = getSampleStyleSheet()
    dark_green = colors.HexColor('#0B4D3E')
    light_grey = colors.HexColor('#F5F7F2')
    medium_grey = colors.HexColor('#64748B')

    # Custom styles
    title_style = ParagraphStyle('CoverTitle', fontSize=24, textColor=dark_green,
                                  spaceAfter=6, alignment=TA_CENTER, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('CoverSubtitle', fontSize=14, textColor=medium_grey,
                                     spaceAfter=4, alignment=TA_CENTER)
    h1_style = ParagraphStyle('H1', fontSize=16, textColor=dark_green,
                               spaceBefore=16, spaceAfter=10, fontName='Helvetica-Bold')
    h2_style = ParagraphStyle('H2', fontSize=13, textColor=dark_green,
                               spaceBefore=12, spaceAfter=8, fontName='Helvetica-Bold')
    body_style = ParagraphStyle('Body', fontSize=10, leading=14, spaceAfter=6)
    small_style = ParagraphStyle('Small', fontSize=8, textColor=medium_grey, spaceAfter=4)

    story = []

    # ---- COVER PAGE ----
    story.append(Spacer(1, 80*mm))
    story.append(Paragraph('CarbonPass', title_style))
    story.append(Paragraph('CBAM Compliance Report', subtitle_style))
    story.append(Spacer(1, 20*mm))
    if company:
        story.append(Paragraph(company.name, ParagraphStyle(
            'CompanyName', fontSize=18, textColor=dark_green,
            spaceAfter=8, alignment=TA_CENTER, fontName='Helvetica-Bold')))
        if company.ssm_number:
            story.append(Paragraph(f'SSM: {company.ssm_number}', subtitle_style))
    story.append(Spacer(1, 15*mm))
    story.append(Paragraph(f'Reporting Period: {period.year} Q{period.quarter}', subtitle_style))
    story.append(Paragraph(f'Generated: {datetime.datetime.now().strftime("%Y-%m-%d")}', subtitle_style))
    story.append(Spacer(1, 30*mm))
    story.append(Paragraph('CONFIDENTIAL', ParagraphStyle(
        'Confidential', fontSize=10, textColor=colors.grey,
        alignment=TA_CENTER, spaceBefore=20)))
    story.append(PageBreak())

    # ---- SECTION 1: COMPANY INFO ----
    story.append(Paragraph('1. Company & Facility Information', h1_style))
    if company:
        info_data = [
            ['Company Name', company.name or 'N/A'],
            ['SSM Registration No.', company.ssm_number or 'N/A'],
            ['State', company.state or 'N/A'],
            ['Industry', INDUSTRY_LABELS['en'].get(company.industry, company.industry or 'N/A')],
            ['Address', company.address or 'N/A'],
            ['Export Markets', ', '.join(company.get_export_markets_list()) or 'N/A'],
        ]
    else:
        info_data = [['Company Name', 'N/A']]

    t = Table(info_data, colWidths=[120, 350])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (0, -1), light_grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    # ---- SECTION 2: METHODOLOGY ----
    story.append(Paragraph('2. Methodology & Boundary', h1_style))
    story.append(Paragraph(
        'This report follows the <b>GHG Protocol Corporate Accounting and Reporting Standard</b> '
        'and complies with the <b>EU CBAM Regulation (EU) 2023/956</b> for calculating embedded emissions '
        'of products imported into the European Union.', body_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph('<b>Scope:</b> Scope 1 (direct emissions from fuel combustion) + Scope 2 (indirect emissions from purchased electricity)', body_style))
    story.append(Paragraph(f'<b>Grid Emission Factor:</b> {calc.grid_factor_used} kg CO₂/kWh ({calc.grid_factor_source})' if calc else 'N/A', body_style))
    story.append(Paragraph(f'<b>Reporting Period:</b> {period.year} Q{period.quarter} ({period.quarter_months()[0]} - {period.quarter_months()[1]})', body_style))
    story.append(Paragraph(f'<b>Calculation Date:</b> {calc.calculated_at.strftime("%Y-%m-%d %H:%M") if calc else "N/A"}', body_style))
    story.append(Spacer(1, 8))

    # ---- SECTION 3: ACTIVITY DATA ----
    story.append(Paragraph('3. Activity Data Summary', h1_style))

    if energy:
        story.append(Paragraph('3.1 Energy Consumption', h2_style))
        energy_rows = [['Energy Type', 'Amount', 'Unit', 'Emission Factor', 'Source']]
        fuels_display = [
            ('Electricity (Grid)', energy.electricity_kwh, 'kWh', f'{calc.grid_factor_used} kg CO₂/kWh' if calc else '', calc.grid_factor_source or ''),
            ('Diesel', energy.diesel_litres, 'L', '2.68 kg CO₂e/L', 'IPCC AR6'),
            ('Petrol', energy.petrol_litres, 'L', '2.31 kg CO₂e/L', 'IPCC AR6'),
            ('Natural Gas', energy.natural_gas_m3, 'm³', '2.00 kg CO₂e/m³', 'IPCC AR6'),
            ('LPG', energy.lpg_kg, 'kg', '2.98 kg CO₂e/kg', 'IPCC AR6'),
            ('Coal', energy.coal_tonne, 'tonne', '2,420 kg CO₂e/kg', 'IPCC AR6'),
        ]
        for label, amount, unit, factor_txt, source_txt in fuels_display:
            if amount and amount > 0:
                energy_rows.append([label, f'{amount:,.2f}', unit, factor_txt, source_txt])

        if energy.other_fuel_amount and energy.other_fuel_amount > 0:
            energy_rows.append([
                energy.other_fuel_label or 'Other Fuel',
                f'{energy.other_fuel_amount:,.2f}',
                energy.other_fuel_unit or '',
                'User defined', 'User defined'
            ])

        if len(energy_rows) > 1:
            t = Table(energy_rows, colWidths=[100, 70, 50, 110, 120])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), dark_green),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(t)
        else:
            story.append(Paragraph('No energy data recorded.', body_style))
    else:
        story.append(Paragraph('No energy data recorded.', body_style))

    story.append(Spacer(1, 8))

    if products:
        story.append(Paragraph('3.2 Production Data', h2_style))
        prod_rows = [['Product Name', 'Amount', 'Unit', 'Export to EU']]
        for p in products:
            prod_rows.append([p.product_name, f'{p.amount:,.2f}', p.unit, 'Yes' if p.export_to_eu else 'No'])
        t = Table(prod_rows, colWidths=[180, 80, 80, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), dark_green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(t)
    story.append(Spacer(1, 10))

    # ---- SECTION 4: EMISSIONS RESULTS ----
    story.append(Paragraph('4. Emissions Results', h1_style))

    if calc:
        # Summary
        story.append(Paragraph('4.1 Emissions Summary', h2_style))
        emm_rows = [
            ['Scope', 'Emissions (tonnes CO₂e)', 'Percentage'],
            ['Scope 1 – Direct (Fuel Combustion)',
             f'{calc.scope1_tonnes:,.2f}',
             f'{(calc.scope1_tonnes/calc.total_tonnes*100):.1f}%' if calc.total_tonnes > 0 else '-'],
            ['Scope 2 – Indirect (Purchased Electricity)',
             f'{calc.scope2_tonnes:,.2f}',
             f'{(calc.scope2_tonnes/calc.total_tonnes*100):.1f}%' if calc.total_tonnes > 0 else '-'],
            ['Total Emissions',
             f'{calc.total_tonnes:,.2f}',
             '100%'],
        ]
        t = Table(emm_rows, colWidths=[180, 140, 100])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), dark_green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(t)
        story.append(Spacer(1, 10))

        # Product emission intensities
        intensities = list(calc.product_intensities)
        if intensities:
            story.append(Paragraph('4.2 Product Emission Intensities (SEE)', h2_style))
            see_rows = [['Product', 'Amount', 'Unit', 'Attributed Emissions\n(tonnes CO₂e)',
                         'Emission Intensity\n(tonnes CO₂e/unit)']]
            for pei in intensities:
                see_rows.append([
                    pei.product_name,
                    f'{pei.amount:,.2f}',
                    pei.unit,
                    f'{pei.attributed_emissions:,.4f}',
                    f'{pei.intensity:,.4f}'
                ])
            t = Table(see_rows, colWidths=[100, 60, 50, 100, 100])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), dark_green),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
            ]))
            story.append(t)

        # Other emissions
        if period.other_emissions and period.other_emissions > 0:
            story.append(Spacer(1, 6))
            story.append(Paragraph(f'<b>Other Known Emissions:</b> {period.other_emissions:,.2f} tonnes CO₂e', body_style))

        story.append(Spacer(1, 8))
        story.append(Paragraph(f'<b>Measurement Uncertainty:</b> ±{calc.uncertainty_pct}%', body_style))
        story.append(Paragraph(f'<b>Data Quality:</b> {calc.data_quality}', body_style))

    story.append(Spacer(1, 10))

    # ---- SECTION 5: DATA QUALITY ----
    story.append(Paragraph('5. Data Quality Statement', h1_style))
    story.append(Paragraph(
        'The data presented in this report is based on actual consumption and production records '
        'provided by the reporting company. All emission factors are sourced from publicly available '
        'databases including the IPCC Sixth Assessment Report (AR6) for fuel combustion factors and '
        'Suruhanjaya Tenaga (Energy Commission of Malaysia) for grid electricity emission factors.',
        body_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        'This report has been prepared in accordance with the EU CBAM Implementing Regulation '
        '(EU) 2023/1773 Annex I data format requirements.',
        body_style))

    # ---- SECTION 6: APPENDIX ----
    story.append(Paragraph('6. Appendix – Emission Factors Used', h1_style))
    if calc and calc.emission_factors_snapshot:
        try:
            factors = json.loads(calc.emission_factors_snapshot)
            fact_rows = [['Energy Type', 'Factor Value', 'Unit', 'Source']]
            for f_data in factors:
                fact_rows.append([
                    f_data.get('type', ''),
                    str(f_data.get('value', '')),
                    f_data.get('unit', ''),
                    f_data.get('source', '')
                ])
            t = Table(fact_rows, colWidths=[90, 80, 90, 160])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), dark_green),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
            ]))
            story.append(t)
            story.append(Spacer(1, 10))
        except Exception:
            pass

    # Disclaimer
    story.append(Spacer(1, 15))
    story.append(Paragraph(
        'DISCLAIMER: This report was generated by CarbonPass software based on data provided by '
        'the reporting company. Emission factors are sourced from publicly available databases '
        'including the IPCC AR6 Guidelines and Suruhanjaya Tenaga Malaysia. The accuracy of this '
        'report depends on the accuracy of the input data. CarbonPass does not guarantee acceptance '
        'of this report by EU customs authorities or third-party verifiers. Third-party verification '
        'is recommended for CBAM compliance declarations.',
        ParagraphStyle('Disclaimer', fontSize=8, textColor=medium_grey, leading=10, spaceBefore=10)
    ))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f'Generated by CarbonPass | carbonpass.com | {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}',
        ParagraphStyle('Footer', fontSize=7, textColor=colors.grey, alignment=TA_CENTER)
    ))

    # Build
    try:
        doc.build(story)
    except Exception as e:
        print(f"PDF build error: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        return None

    file_size = os.path.getsize(filepath)

    # Save report record
    report = Report(
        period_id=period.id,
        user_id=period.user_id,
        type='cbam',
        language='en',
        filename=safe_filename,
        display_name=display_name,
        file_size=file_size
    )
    db.session.add(report)
    db.session.commit()

    return report

# ============================================================
# GHG SUMMARY PDF GENERATOR (per PRD Section 4.5.1 Type B)
# ============================================================

def generate_ghg_summary_pdf(period_id, language='zh'):
    """Generate GHG Protocol corporate summary report (bilingual)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    period = ReportingPeriod.query.get(period_id)
    if not period:
        return None
    calc = period.calculation_result
    company = period.company
    energy = period.energy_inputs
    products = list(period.production_inputs)

    safe_filename = f"{uuid.uuid4().hex}.pdf"
    filepath = os.path.join(REPORTS_FOLDER, safe_filename)
    company_name_safe = company.name.replace(' ', '_') if company else 'Unknown'
    report_type_label = 'GHG_Summary'
    display_name = f"CarbonPass_{company_name_safe}_{report_type_label}_{period.year}_Q{period.quarter}.pdf"

    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        topMargin=20*mm, bottomMargin=20*mm,
        leftMargin=25*mm, rightMargin=25*mm
    )

    dark_green = colors.HexColor('#0B4D3E')
    teal = colors.HexColor('#14B8A6')
    light_grey = colors.HexColor('#F5F7F2')
    medium_grey = colors.HexColor('#64748B')

    is_zh = (language == 'zh')

    title_style = ParagraphStyle('Title', fontSize=22, textColor=dark_green,
                                  spaceAfter=6, alignment=TA_CENTER, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('Subtitle', fontSize=12, textColor=medium_grey,
                                     spaceAfter=4, alignment=TA_CENTER)
    h1_style = ParagraphStyle('H1', fontSize=15, textColor=dark_green,
                               spaceBefore=14, spaceAfter=8, fontName='Helvetica-Bold')
    h2_style = ParagraphStyle('H2', fontSize=12, textColor=dark_green,
                               spaceBefore=10, spaceAfter=6, fontName='Helvetica-Bold')
    body_style = ParagraphStyle('Body', fontSize=10, leading=14, spaceAfter=5)
    small_style = ParagraphStyle('Small', fontSize=8, textColor=medium_grey, spaceAfter=4)

    story = []

    # COVER
    story.append(Spacer(1, 60*mm))
    story.append(Paragraph('CarbonPass', title_style))
    story.append(Paragraph(
        '温室气体排放摘要报告' if is_zh else 'Greenhouse Gas Emission Summary Report',
        subtitle_style))
    story.append(Paragraph(
        'GHG Protocol Corporate Standard' if not is_zh else 'GHG Protocol 企业核算与报告标准',
        ParagraphStyle('Std', fontSize=10, textColor=teal, spaceAfter=4, alignment=TA_CENTER)))
    story.append(Spacer(1, 16*mm))
    if company:
        story.append(Paragraph(company.name, ParagraphStyle(
            'Co', fontSize=16, textColor=dark_green, spaceAfter=6,
            alignment=TA_CENTER, fontName='Helvetica-Bold')))
        if company.ssm_number:
            story.append(Paragraph(
                f'{"注册号" if is_zh else "SSM"}: {company.ssm_number}', subtitle_style))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(
        f'{"填报周期" if is_zh else "Reporting Period"}: {period.year} Q{period.quarter}', subtitle_style))
    story.append(Paragraph(
        f'{"生成日期" if is_zh else "Generated"}: {datetime.datetime.now().strftime("%Y-%m-%d")}', subtitle_style))
    story.append(PageBreak())

    # SECTION 1: COMPANY
    story.append(Paragraph(
        '1. 企业基本信息' if is_zh else '1. Company Information', h1_style))
    if company:
        rows = [
            ['企业名称' if is_zh else 'Company Name', company.name or 'N/A'],
            ['注册号（SSM）' if is_zh else 'SSM No.', company.ssm_number or 'N/A'],
            ['所在州属' if is_zh else 'State', company.state or 'N/A'],
            ['主营行业' if is_zh else 'Industry',
             INDUSTRY_LABELS['zh' if is_zh else 'en'].get(company.industry, company.industry or 'N/A')],
            ['工厂地址' if is_zh else 'Address', company.address or 'N/A'],
            ['出口市场' if is_zh else 'Export Markets',
             ', '.join(company.get_export_markets_list()) or 'N/A'],
        ]
        t = Table(rows, colWidths=[110, 360])
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 0), (0, -1), light_grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(t)
    story.append(Spacer(1, 8))

    # SECTION 2: METHODOLOGY
    story.append(Paragraph(
        '2. 核算方法论' if is_zh else '2. Accounting Methodology', h1_style))
    story.append(Paragraph(
        '本报告遵循《温室气体议定书企业核算与报告标准》（GHG Protocol Corporate Accounting and Reporting Standard），核算范围涵盖 Scope 1（直接排放）和 Scope 2（外购能源间接排放）。' if is_zh else
        'This report follows the GHG Protocol Corporate Accounting and Reporting Standard, covering Scope 1 (direct emissions) and Scope 2 (indirect emissions from purchased electricity).',
        body_style))
    story.append(Paragraph(
        f'{"核算周期" if is_zh else "Reporting Period"}: {period.year} Q{period.quarter} ({period.quarter_months()[0]} – {period.quarter_months()[1]})',
        body_style))
    if calc:
        story.append(Paragraph(
            f'{"电网排放因子" if is_zh else "Grid Factor"}: {calc.grid_factor_used} kg CO₂/kWh ({calc.grid_factor_source})',
            body_style))
        story.append(Paragraph(
            f'{"燃料因子来源" if is_zh else "Fuel Factors"}: IPCC AR6 Guidelines',
            body_style))
    story.append(Spacer(1, 8))

    # SECTION 3: ACTIVITY DATA
    story.append(Paragraph(
        '3. 活动数据汇总' if is_zh else '3. Activity Data Summary', h1_style))

    if energy:
        story.append(Paragraph('3.1 ' + ('能源消耗明细' if is_zh else 'Energy Consumption'), h2_style))
        hdr = ['能源类型' if is_zh else 'Energy', '消耗量' if is_zh else 'Amount',
               '单位' if is_zh else 'Unit', '排放因子' if is_zh else 'Factor']
        rows = [hdr]
        fuels_en = [
            ('外购电力' if is_zh else 'Electricity', energy.electricity_kwh, 'kWh',
             f'{calc.grid_factor_used} kg CO₂/kWh' if calc else ''),
            ('柴油' if is_zh else 'Diesel', energy.diesel_litres, 'L', '2.68 kg CO₂e/L'),
            ('汽油' if is_zh else 'Petrol', energy.petrol_litres, 'L', '2.31 kg CO₂e/L'),
            ('天然气' if is_zh else 'Natural Gas', energy.natural_gas_m3, 'm³', '2.00 kg CO₂e/m³'),
            ('LPG', energy.lpg_kg, 'kg', '2.98 kg CO₂e/kg'),
            ('煤炭' if is_zh else 'Coal', energy.coal_tonne, '吨' if is_zh else 'tonne', '2.42 kg CO₂e/kg'),
        ]
        for label, amount, unit, factor_txt in fuels_en:
            if amount and amount > 0:
                rows.append([label, f'{amount:,.2f}', unit, factor_txt])
        if energy.other_fuel_amount and energy.other_fuel_amount > 0:
            rows.append([
                energy.other_fuel_label or ('其他燃料' if is_zh else 'Other'),
                f'{energy.other_fuel_amount:,.2f}',
                energy.other_fuel_unit or '', '—'
            ])
        if len(rows) > 1:
            t = Table(rows, colWidths=[120, 80, 60, 160])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), dark_green),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(t)

    if products:
        story.append(Paragraph('3.2 ' + ('生产数据' if is_zh else 'Production Data'), h2_style))
        prod_hdr = ['产品名称' if is_zh else 'Product', '产量' if is_zh else 'Amount',
                    '单位' if is_zh else 'Unit', '出口欧盟' if is_zh else 'EU Export']
        prod_rows = [prod_hdr]
        for p in products:
            prod_rows.append([p.product_name, f'{p.amount:,.2f}', p.unit,
                               ('是' if is_zh else 'Yes') if p.export_to_eu else ('否' if is_zh else 'No')])
        t = Table(prod_rows, colWidths=[200, 80, 80, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), dark_green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
        ]))
        story.append(t)
    story.append(Spacer(1, 10))

    # SECTION 4: EMISSIONS RESULTS
    story.append(Paragraph(
        '4. 温室气体排放结果' if is_zh else '4. GHG Emission Results', h1_style))

    if calc:
        scope_hdr = ['核算范围' if is_zh else 'Scope', '排放量（吨 CO₂e）' if is_zh else 'Emissions (tCO₂e)', '占比' if is_zh else '%']
        scope_rows = [scope_hdr,
            ['Scope 1 – ' + ('直接排放（化石燃料燃烧）' if is_zh else 'Direct (Fuel Combustion)'),
             f'{calc.scope1_tonnes:,.2f}',
             f'{(calc.scope1_tonnes/calc.total_tonnes*100):.1f}%' if calc.total_tonnes > 0 else '—'],
            ['Scope 2 – ' + ('外购电力间接排放' if is_zh else 'Indirect (Purchased Electricity)'),
             f'{calc.scope2_tonnes:,.2f}',
             f'{(calc.scope2_tonnes/calc.total_tonnes*100):.1f}%' if calc.total_tonnes > 0 else '—'],
            [('合计 / Total' if is_zh else 'Total Emissions'), f'{calc.total_tonnes:,.2f}', '100%'],
        ]
        t = Table(scope_rows, colWidths=[200, 120, 100])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), dark_green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
        ]))
        story.append(t)
        story.append(Spacer(1, 10))

        intensities = list(calc.product_intensities)
        if intensities:
            story.append(Paragraph(
                '4.2 产品排放强度（SEE）' if is_zh else '4.2 Product Emission Intensities (SEE)', h2_style))
            see_hdr = ['产品' if is_zh else 'Product', '产量' if is_zh else 'Amount',
                       '单位' if is_zh else 'Unit',
                       '归因排放（tCO₂e）' if is_zh else 'Attributed (tCO₂e)',
                       '排放强度' if is_zh else 'Intensity (tCO₂e/unit)']
            see_rows = [see_hdr]
            for pei in intensities:
                see_rows.append([pei.product_name, f'{pei.amount:,.2f}', pei.unit,
                                  f'{pei.attributed_emissions:,.4f}', f'{pei.intensity:,.4f}'])
            t = Table(see_rows, colWidths=[100, 60, 50, 110, 100])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), dark_green),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_grey]),
                ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
            ]))
            story.append(t)

    # SECTION 5: DATA QUALITY
    story.append(Paragraph(
        '5. 数据质量声明' if is_zh else '5. Data Quality Statement', h1_style))
    story.append(Paragraph(
        '本报告数据基于企业实际消耗记录和生产数据，排放因子来源于 IPCC AR6 指南及马来西亚 Suruhanjaya Tenaga（ST）年度电网因子报告，计算方法论符合 GHG Protocol 企业核算标准。企业授权代表确认上述数据真实可靠。' if is_zh else
        'Data in this report is based on actual consumption and production records. Emission factors are sourced from IPCC AR6 and Suruhanjaya Tenaga Malaysia annual reports. The methodology complies with the GHG Protocol Corporate Standard. The data has been confirmed by an authorised company representative.',
        body_style))
    if calc:
        story.append(Paragraph(
            f'{"测量不确定度" if is_zh else "Measurement Uncertainty"}: ±{calc.uncertainty_pct}%', body_style))

    # Disclaimer
    story.append(Spacer(1, 15))
    story.append(Paragraph(
        'DISCLAIMER: This report was generated by CarbonPass software based on data provided by the reporting company. Emission factors are sourced from publicly available databases. The accuracy of this report depends on the accuracy of input data.',
        ParagraphStyle('Disc', fontSize=8, textColor=medium_grey, leading=10, spaceBefore=10)))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f'Generated by CarbonPass | carbonpass.com | {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}',
        ParagraphStyle('Footer', fontSize=7, textColor=colors.grey, alignment=TA_CENTER)))

    try:
        doc.build(story)
    except Exception as e:
        print(f"GHG PDF error: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        return None

    file_size = os.path.getsize(filepath)
    report = Report(
        period_id=period.id,
        user_id=period.user_id,
        type='ghg_summary',
        language=language,
        filename=safe_filename,
        display_name=display_name,
        file_size=file_size
    )
    db.session.add(report)
    db.session.commit()
    return report

# ============================================================
# ROUTES: HOME / PUBLIC
# ============================================================

@app.route('/_debug_google')
def debug_google():
    import os
    cid = os.environ.get('GOOGLE_CLIENT_ID', '')
    return jsonify({
        'GOOGLE_OAUTH_ENABLED': GOOGLE_OAUTH_ENABLED,
        'CID_SET': bool(cid),
        'CID_PREFIX': cid[:20] if cid else '',
        'CWD': os.getcwd(),
        'ENV_FILE_EXISTS': os.path.exists('.env'),
    })

@app.route('/')
def home():
    lang = get_lang()
    if lang == 'en':
        return render_template('home_en.html')
    return render_template('home.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

# ============================================================
# ROUTES: AUTH
# ============================================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    lang = get_lang()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        position = request.form.get('position', '').strip()
        phone = request.form.get('phone', '').strip()

        # Validation
        errors = []
        if not name:
            errors.append('请填写姓名' if lang == 'zh' else 'Name is required')
        if not email:
            errors.append('请填写邮箱' if lang == 'zh' else 'Email is required')
        else:
            try:
                email_validator.validate_email(email)
            except Exception:
                errors.append('邮箱格式无效' if lang == 'zh' else 'Invalid email format')
        if not password or len(password) < 8:
            errors.append('密码至少8位' if lang == 'zh' else 'Password must be at least 8 characters')
        elif not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
            errors.append('密码需包含字母和数字' if lang == 'zh' else 'Password must contain letters and numbers')
        if password != confirm:
            errors.append('两次密码不一致' if lang == 'zh' else 'Passwords do not match')
        if User.query.filter_by(email=email).first():
            errors.append('该邮箱已注册，请直接登录' if lang == 'zh' else 'Email already registered')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register.html')

        user = User(
            email=email, name=name,
            position=position, phone=phone,
            role='user', email_verified=True  # Skip email verification for MVP
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user, remember=True)
        flash('注册成功！' if lang == 'zh' else 'Registration successful!', 'success')
        return redirect(url_for('company_profile'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    lang = get_lang()
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(email=email).first()

        if not user:
            flash('邮箱未注册' if lang == 'zh' else 'Email not registered', 'error')
            return render_template('login.html')

        if user.is_locked():
            remaining = (user.locked_until - datetime.datetime.utcnow()).seconds // 60
            flash(
                f'账户已锁定，请{remaining}分钟后重试' if lang == 'zh'
                else f'Account locked. Try again in {remaining} minutes',
                'error'
            )
            return render_template('login.html')

        if not user.password_hash or not user.check_password(password):
            user.login_attempts = (user.login_attempts or 0) + 1
            if user.login_attempts >= 5:
                user.locked_until = datetime.datetime.utcnow() + timedelta(minutes=15)
                flash('密码错误次数过多，账户已锁定15分钟' if lang == 'zh'
                      else 'Too many failed attempts. Account locked for 15 min', 'error')
            else:
                remaining = 5 - user.login_attempts
                flash(
                    f'密码错误，还剩{remaining}次机会' if lang == 'zh'
                    else f'Wrong password. {remaining} attempts remaining',
                    'error'
                )
            db.session.commit()
            return render_template('login.html')

        # Success
        user.login_attempts = 0
        user.locked_until = None
        db.session.commit()

        login_user(user, remember=remember)
        flash('登录成功！' if lang == 'zh' else 'Login successful!', 'success')

        next_page = request.args.get('next')
        if next_page:
            return redirect(next_page)
        return redirect(url_for('dashboard'))

    return render_template('login.html')

@app.route('/logout')
def logout_page():
    logout_user()
    session.clear()
    return redirect(url_for('home'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    lang = get_lang()
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(user.id)
            reset_url = url_for('reset_password', token=token, _external=True)
            print(f"\n=== PASSWORD RESET EMAIL (DEV MODE) ===")
            print(f"To: {email}")
            print(f"Reset URL: {reset_url}")
            print(f"=========================================\n")
        flash(
            '如果该邮箱已注册，您将收到重置密码的邮件' if lang == 'zh'
            else 'If the email is registered, you will receive a password reset link',
            'info'
        )
        return redirect(url_for('login_page'))
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    lang = get_lang()
    user = verify_reset_token(token)
    if not user:
        flash('链接已过期或无效' if lang == 'zh' else 'Invalid or expired reset link', 'error')
        return redirect(url_for('login_page'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not password or len(password) < 8:
            flash('密码至少8位' if lang == 'zh' else 'Password must be at least 8 characters', 'error')
        elif password != confirm:
            flash('两次密码不一致' if lang == 'zh' else 'Passwords do not match', 'error')
        else:
            user.set_password(password)
            user.login_attempts = 0
            user.locked_until = None
            db.session.commit()
            flash('密码已重置，请登录' if lang == 'zh' else 'Password reset successfully', 'success')
            return redirect(url_for('login_page'))

    return render_template('reset_password.html', token=token)

@app.route('/login/google/callback')
def login_google_callback():
    lang = get_lang()
    if not google.authorized:
        flash('Google 授权未完成，请重试' if lang == 'zh' else 'Google authorisation incomplete, please try again', 'error')
        return redirect(url_for('login_page'))
    try:
        resp = google.get('/oauth2/v2/userinfo')
        if not resp.ok:
            raise Exception(f"userinfo API error: {resp.status_code}")
        info = resp.json()
        gid = info.get('id')
        email = info.get('email', '').lower()
        name = info.get('name', email)
        avatar = info.get('picture', '')

        if not email:
            flash('无法获取 Google 邮箱信息' if lang == 'zh' else 'Could not retrieve email from Google', 'error')
            return redirect(url_for('login_page'))

        # 先按 google_id 查，再按 email 查（防止同邮箱重复注册）
        user = User.query.filter_by(google_id=gid).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if user:
                # 已有邮箱账号，绑定 Google ID
                user.google_id = gid
                user.avatar = avatar or user.avatar
            else:
                # 全新用户
                user = User(
                    google_id=gid, email=email, name=name,
                    avatar=avatar, role='user', email_verified=True
                )
                db.session.add(user)

        user.name = name
        user.avatar = avatar or user.avatar
        db.session.commit()
        login_user(user, remember=True)
        flash('Google 登录成功！' if lang == 'zh' else 'Signed in with Google!', 'success')
        return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"[Google OAuth] error: {e}")
        flash('Google 登录失败，请重试' if lang == 'zh' else 'Google login failed, please try again', 'error')
        return redirect(url_for('login_page'))

# ============================================================
# ROUTES: COMPANY PROFILE
# ============================================================

@app.route('/company/profile', methods=['GET', 'POST'])
@login_required
def company_profile():
    lang = get_lang()
    company = Company.query.filter_by(user_id=current_user.id).first()

    if request.method == 'POST':
        if not company:
            company = Company(user_id=current_user.id)
            db.session.add(company)

        company.name = request.form.get('name', '')
        company.name_en = request.form.get('name_en', '')
        company.ssm_number = request.form.get('ssm_number', '')
        company.state = request.form.get('state', '')
        company.address = request.form.get('address', '')
        company.industry = request.form.get('industry', '')
        export_mkts = request.form.getlist('export_markets')
        company.export_markets = ','.join(export_mkts) if export_mkts else ''
        company.website = request.form.get('website', '')
        company.contact_phone = request.form.get('contact_phone', '')
        company.product_name = request.form.get('product_name', '')
        company.product_unit = request.form.get('product_unit', 'tonnes')
        db.session.commit()
        flash('企业信息已保存' if lang == 'zh' else 'Company info saved', 'success')
        return redirect(url_for('company_profile'))

    return render_template('company_profile.html', company=company)

# ============================================================
# ROUTES: DATA ENTRY (4-STEP WIZARD)
# ============================================================

@app.route('/data-entry')
@login_required
def data_entry_index():
    """List reporting periods and create new ones."""
    lang = get_lang()
    periods = ReportingPeriod.query.filter_by(user_id=current_user.id)\
        .order_by(ReportingPeriod.year.desc(), ReportingPeriod.quarter.desc()).all()
    return render_template('data_entry_index.html', periods=periods)

@app.route('/data-entry/new', methods=['GET', 'POST'])
@login_required
def data_entry_new():
    lang = get_lang()
    company = Company.query.filter_by(user_id=current_user.id).first()
    if not company:
        flash('请先完善企业信息' if lang == 'zh' else 'Please complete company profile first', 'warning')
        return redirect(url_for('company_profile'))

    if request.method == 'POST':
        year = int(request.form.get('year', datetime.datetime.now().year))
        quarter = int(request.form.get('quarter', 1))

        existing = ReportingPeriod.query.filter_by(
            user_id=current_user.id, year=year, quarter=quarter
        ).first()
        if existing:
            flash(
                f'{year}年Q{quarter}已有填报记录，正在打开编辑' if lang == 'zh'
                else f'{year} Q{quarter} already exists. Opening for edit.',
                'info'
            )
            return redirect(url_for('data_entry_step1', period_id=existing.id))

        period = ReportingPeriod(
            user_id=current_user.id,
            company_id=company.id,
            year=year, quarter=quarter,
            status='draft'
        )
        db.session.add(period)
        db.session.flush()

        # Create empty energy input
        energy = EnergyInput(period_id=period.id)
        db.session.add(energy)
        db.session.commit()

        return redirect(url_for('data_entry_step1', period_id=period.id))

    return render_template('data_entry_new.html')

@app.route('/data-entry/<int:period_id>/step1', methods=['GET', 'POST'])
@login_required
def data_entry_step1(period_id):
    lang = get_lang()
    period = ReportingPeriod.query.get_or_404(period_id)
    if period.user_id != current_user.id:
        abort(403)
    energy = EnergyInput.query.filter_by(period_id=period.id).first()
    if not energy:
        energy = EnergyInput(period_id=period.id)
        db.session.add(energy)
        db.session.commit()

    if request.method == 'POST':
        energy.electricity_kwh = float(request.form.get('electricity_kwh', 0) or 0)
        energy.diesel_litres = float(request.form.get('diesel_litres', 0) or 0)
        energy.petrol_litres = float(request.form.get('petrol_litres', 0) or 0)
        energy.natural_gas_m3 = float(request.form.get('natural_gas_m3', 0) or 0)
        energy.lpg_kg = float(request.form.get('lpg_kg', 0) or 0)
        energy.coal_tonne = float(request.form.get('coal_tonne', 0) or 0)
        energy.other_fuel_label = request.form.get('other_fuel_label', '')
        energy.other_fuel_amount = float(request.form.get('other_fuel_amount', 0) or 0)
        energy.other_fuel_unit = request.form.get('other_fuel_unit', '')
        db.session.commit()
        flash('能源数据已保存' if lang == 'zh' else 'Energy data saved', 'success')
        return redirect(url_for('data_entry_step2', period_id=period.id))

    return render_template('data_entry_step1.html', period=period, energy=energy)

@app.route('/data-entry/<int:period_id>/step2', methods=['GET', 'POST'])
@login_required
def data_entry_step2(period_id):
    lang = get_lang()
    period = ReportingPeriod.query.get_or_404(period_id)
    if period.user_id != current_user.id:
        abort(403)
    products = list(ProductionInput.query.filter_by(period_id=period.id).all())

    if request.method == 'POST':
        # Delete existing and re-add
        ProductionInput.query.filter_by(period_id=period.id).delete()
        product_names = request.form.getlist('product_name[]')
        amounts = request.form.getlist('amount[]')
        units = request.form.getlist('unit[]')
        export_eus = request.form.getlist('export_to_eu[]')

        for i in range(len(product_names)):
            if product_names[i].strip() and float(amounts[i] or 0) > 0:
                pi = ProductionInput(
                    period_id=period.id,
                    product_name=product_names[i].strip(),
                    amount=float(amounts[i] or 0),
                    unit=units[i] if i < len(units) else 'tonnes',
                    export_to_eu=(export_eus[i] == 'on') if i < len(export_eus) else False
                )
                db.session.add(pi)
        db.session.commit()
        flash('生产数据已保存' if lang == 'zh' else 'Production data saved', 'success')
        return redirect(url_for('data_entry_step3', period_id=period.id))

    # Ensure at least one empty row for new entries
    if not products:
        products = [None]

    return render_template('data_entry_step2.html', period=period, products=products)

@app.route('/data-entry/<int:period_id>/step3', methods=['GET', 'POST'])
@login_required
def data_entry_step3(period_id):
    lang = get_lang()
    period = ReportingPeriod.query.get_or_404(period_id)
    if period.user_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        period.other_emissions = float(request.form.get('other_emissions', 0) or 0)
        db.session.commit()
        flash('数据已保存' if lang == 'zh' else 'Data saved', 'success')
        return redirect(url_for('data_entry_step4', period_id=period.id))

    return render_template('data_entry_step3.html', period=period)

@app.route('/data-entry/<int:period_id>/step4', methods=['GET', 'POST'])
@login_required
def data_entry_step4(period_id):
    lang = get_lang()
    period = ReportingPeriod.query.get_or_404(period_id)
    if period.user_id != current_user.id:
        abort(403)
    energy = EnergyInput.query.filter_by(period_id=period.id).first()
    products = list(ProductionInput.query.filter_by(period_id=period.id).all())

    if request.method == 'POST':
        confirmation = request.form.get('confirmation') == 'on'
        if not confirmation:
            flash('请确认数据真实准确' if lang == 'zh' else 'Please confirm data accuracy', 'warning')
        elif not products:
            flash('请至少添加一种产品' if lang == 'zh' else 'Add at least one product', 'warning')
        else:
            period.confirmation = True
            period.status = 'submitted'
            db.session.commit()

            # Calculate emissions
            calc_result = calculate_emissions(period)

            if calc_result:
                flash('计算完成！' if lang == 'zh' else 'Calculation complete!', 'success')
                return redirect(url_for('view_result', period_id=period.id))
            else:
                flash('计算失败，请检查数据' if lang == 'zh' else 'Calculation failed', 'error')

    return render_template('data_entry_step4.html', period=period, energy=energy, products=products)

# ============================================================
# ROUTES: RESULTS
# ============================================================

@app.route('/results')
@login_required
def results_list():
    lang = get_lang()
    periods = ReportingPeriod.query.filter_by(user_id=current_user.id)\
        .order_by(ReportingPeriod.year.desc(), ReportingPeriod.quarter.desc()).all()
    return render_template('results_list.html', periods=periods)

@app.route('/results/<int:period_id>')
@login_required
def view_result(period_id):
    lang = get_lang()
    period = ReportingPeriod.query.get_or_404(period_id)
    if period.user_id != current_user.id:
        abort(403)
    calc = period.calculation_result
    if not calc:
        flash('该周期尚未完成核算' if lang == 'zh' else 'Calculation not yet completed', 'warning')
        return redirect(url_for('results_list'))

    intensities = list(calc.product_intensities)
    details = json.loads(calc.calculation_details) if calc.calculation_details else {}
    factors = json.loads(calc.emission_factors_snapshot) if calc.emission_factors_snapshot else []

    return render_template('view_result.html', period=period, calc=calc,
                           intensities=intensities, details=details, factors=factors)

# ============================================================
# ROUTES: REPORTS
# ============================================================

@app.route('/reports')
@login_required
def reports_list():
    lang = get_lang()
    reports = Report.query.filter_by(user_id=current_user.id)\
        .order_by(Report.created_at.desc()).all()
    return render_template('reports_list.html', reports=reports)

@app.route('/reports/generate/<int:period_id>', methods=['POST'])
@login_required
def generate_report(period_id):
    lang = get_lang()
    period = ReportingPeriod.query.get_or_404(period_id)
    if period.user_id != current_user.id:
        abort(403)
    if period.status != 'calculated':
        flash('请先完成核算' if lang == 'zh' else 'Please complete calculation first', 'warning')
        return redirect(url_for('view_result', period_id=period.id))

    report_type = request.form.get('report_type', 'cbam')
    report_lang = request.form.get('report_lang', 'en')

    if report_type == 'ghg':
        report = generate_ghg_summary_pdf(period.id, language=report_lang)
        success_msg = ('GHG 摘要报告已生成！' if lang == 'zh' else 'GHG Summary Report generated!')
    else:
        report = generate_cbam_pdf(period.id)
        success_msg = ('CBAM 报告已生成！' if lang == 'zh' else 'CBAM Report generated!')

    if report:
        flash(success_msg, 'success')
    else:
        flash('报告生成失败' if lang == 'zh' else 'Report generation failed', 'error')

    return redirect(url_for('reports_list'))

@app.route('/reports/download/<int:report_id>')
@login_required
def download_report(report_id):
    report = Report.query.get_or_404(report_id)
    if report.user_id != current_user.id:
        abort(403)
    filepath = os.path.join(REPORTS_FOLDER, report.filename)
    if not os.path.exists(filepath):
        flash('文件不存在' if get_lang() == 'zh' else 'File not found', 'error')
        return redirect(url_for('reports_list'))
    return send_from_directory(
        REPORTS_FOLDER, report.filename,
        as_attachment=True,
        download_name=report.display_name
    )

# ============================================================
# ROUTES: DASHBOARD
# ============================================================

@app.route('/dashboard')
@login_required
def dashboard():
    lang = get_lang()
    company = Company.query.filter_by(user_id=current_user.id).first()

    # Stats
    periods = ReportingPeriod.query.filter_by(user_id=current_user.id).all()
    total_reports = Report.query.filter_by(user_id=current_user.id).count()

    # YTD emissions
    current_year = datetime.datetime.now().year
    ytd_periods = [p for p in periods if p.year == current_year and p.calculation_result]
    ytd_emissions = sum(p.calculation_result.total_tonnes for p in ytd_periods if p.calculation_result)

    # Latest intensity
    latest_intensity = None
    latest_unit = ''
    calculated = [p for p in periods if p.status == 'calculated' and p.calculation_result]
    if calculated:
        latest = sorted(calculated, key=lambda p: (p.year, p.quarter), reverse=True)[0]
        if latest.calculation_result:
            intensities = list(latest.calculation_result.product_intensities)
            if intensities:
                latest_intensity = intensities[0].intensity
                latest_unit = intensities[0].unit

    # Pending entries
    pending = len([p for p in periods if p.status == 'draft'])

    # Check current quarter
    now = datetime.datetime.now()
    current_q = (now.month - 1) // 3 + 1
    has_current = any(p.year == now.year and p.quarter == current_q for p in periods)

    # Trend data (last 4 quarters)
    trend_labels = []
    trend_scope1 = []
    trend_scope2 = []
    trend_total = []

    sorted_periods = sorted(
        [p for p in periods if p.calculation_result],
        key=lambda p: (p.year, p.quarter)
    )[-4:]

    for p in sorted_periods:
        trend_labels.append(f"{p.year} Q{p.quarter}")
        trend_scope1.append(p.calculation_result.scope1_tonnes)
        trend_scope2.append(p.calculation_result.scope2_tonnes)
        trend_total.append(p.calculation_result.total_tonnes)

    return render_template('dashboard.html',
        company=company,
        ytd_emissions=round(ytd_emissions, 2),
        latest_intensity=latest_intensity,
        latest_unit=latest_unit,
        pending=pending,
        total_reports=total_reports,
        has_current=has_current,
        current_year=now.year,
        current_q=current_q,
        trend_labels=json.dumps(trend_labels),
        trend_scope1=json.dumps(trend_scope1),
        trend_scope2=json.dumps(trend_scope2),
        trend_total=json.dumps(trend_total)
    )

# ============================================================
# ROUTES: ADMIN
# ============================================================

@app.route('/admin')
@login_required
@admin_required
def admin_index():
    lang = get_lang()
    user_count = User.query.count()
    company_count = Company.query.count()
    period_count = ReportingPeriod.query.count()
    report_count = Report.query.count()
    return render_template('admin_index.html', user_count=user_count,
                           company_count=company_count,
                           period_count=period_count, report_count=report_count)

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/periods')
@login_required
@admin_required
def admin_periods():
    periods = ReportingPeriod.query.order_by(
        ReportingPeriod.year.desc(), ReportingPeriod.quarter.desc()
    ).all()
    return render_template('admin_periods.html', periods=periods)

@app.route('/admin/factors', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_factors():
    lang = get_lang()
    if request.method == 'POST':
        category = request.form.get('category')
        subcategory = request.form.get('subcategory')
        factor = float(request.form.get('factor', 0))
        unit = request.form.get('unit')
        source = request.form.get('source')
        year = request.form.get('year', type=int)

        ef = EmissionFactor(
            category=category, subcategory=subcategory,
            factor=round(factor, 8), unit=unit,
            source=source, year=year, is_active=True
        )
        db.session.add(ef)
        db.session.commit()
        flash('排放因子已添加' if lang == 'zh' else 'Emission factor added', 'success')
        return redirect(url_for('admin_factors'))

    factors = EmissionFactor.query.order_by(EmissionFactor.category, EmissionFactor.subcategory).all()
    return render_template('admin_factors.html', factors=factors)

@app.route('/admin/factors/<int:factor_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_factor_delete(factor_id):
    ef = EmissionFactor.query.get_or_404(factor_id)
    db.session.delete(ef)
    db.session.commit()
    flash('排放因子已删除' if get_lang() == 'zh' else 'Emission factor deleted', 'success')
    return redirect(url_for('admin_factors'))

# ============================================================
# ROUTES: API
# ============================================================

@app.route('/api/dashboard/trend')
@login_required
def api_trend():
    periods = ReportingPeriod.query.filter_by(user_id=current_user.id).all()
    sorted_p = sorted(
        [p for p in periods if p.calculation_result],
        key=lambda p: (p.year, p.quarter)
    )[-4:]
    data = []
    for p in sorted_p:
        data.append({
            'label': f"{p.year} Q{p.quarter}",
            'scope1': p.calculation_result.scope1_tonnes,
            'scope2': p.calculation_result.scope2_tonnes,
            'total': p.calculation_result.total_tonnes
        })
    return jsonify(data)

@app.route('/api/calculate', methods=['POST'])
def api_calculate():
    """Quick calculation API (for external use)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    electricity_kwh = float(data.get('electricity_kwh', 0))
    diesel_litres = float(data.get('diesel_litres', 0))
    natural_gas_m3 = float(data.get('natural_gas_m3', 0))
    lpg_kg = float(data.get('lpg_kg', 0))
    coal_tonne = float(data.get('coal_tonne', 0))
    state = data.get('state', 'Selangor')
    products = data.get('products', [])

    region = STATE_TO_REGION.get(state, 'peninsular')
    grid_info = GRID_FACTORS[region]
    grid_factor = grid_info['factor']

    # Scope 1
    scope1 = 0
    details = []
    fuels = [
        ('diesel', diesel_litres, FUEL_FACTORS['diesel']['factor']),
        ('natural_gas', natural_gas_m3, FUEL_FACTORS['natural_gas']['factor']),
        ('lpg', lpg_kg, FUEL_FACTORS['lpg']['factor']),
    ]
    for name, amount, factor in fuels:
        if amount > 0:
            e = amount * factor / 1000
            scope1 += e
            details.append({name: round(e, 4)})
    if coal_tonne > 0:
        e = coal_tonne * 1000 * FUEL_FACTORS['coal']['factor'] / 1000
        scope1 += e
        details.append({'coal': round(e, 4)})

    # Scope 2
    scope2 = electricity_kwh * grid_factor / 1000 if electricity_kwh > 0 else 0

    total = scope1 + scope2

    # Product allocation
    product_results = []
    if products:
        total_prod = sum(float(p.get('amount', 0)) for p in products)
        for p in products:
            amt = float(p.get('amount', 0))
            if total_prod > 0:
                attr = total * (amt / total_prod)
                intensity = attr / amt if amt > 0 else 0
            else:
                attr = total / len(products)
                intensity = attr / amt if amt > 0 else 0
            product_results.append({
                'name': p.get('name', ''),
                'amount': amt,
                'attributed_emissions': round(attr, 4),
                'intensity': round(intensity, 4)
            })

    return jsonify({
        'scope1_tonnes': round(scope1, 2),
        'scope2_tonnes': round(scope2, 2),
        'total_tonnes': round(total, 2),
        'grid_factor': grid_factor,
        'region': region,
        'product_results': product_results,
        'details': details
    })

# ============================================================
# LEGACY ROUTES (preserved)
# ============================================================

@app.route('/carbon')
def carbon():
    """Carbon footprint calculator (legacy)."""
    return render_template('carbon.html')

@app.route('/cbam', methods=['GET', 'POST'])
def cbam_report():
    """CBAM report (legacy single-form version)."""
    from cbam_calculator import CBAMCalculator
    from report_generator import ReportGenerator

    lang = get_lang()
    calculator = CBAMCalculator()
    generator = ReportGenerator()
    result_data = None

    if request.method == 'POST':
        company_name = request.form.get('company_name', '')
        facility_id = request.form.get('facility_id', '')
        facility_address = request.form.get('facility_address', '')
        contact_person = request.form.get('contact_person', '')
        contact_email = request.form.get('contact_email', '')
        reporting_period = request.form.get('reporting_period', '2026-Q1')
        production_period = request.form.get('production_period', '')
        product_type = request.form.get('product_type', 'steel')
        product_description = request.form.get('product_description', '')
        quantity = float(request.form.get('quantity', 0) or 0)
        unit = request.form.get('unit', 'tonnes')
        electricity_kwh = float(request.form.get('electricity_kwh', 0) or 0)
        diesel_litres = float(request.form.get('diesel_litres', 0) or 0)
        natural_gas_m3 = float(request.form.get('natural_gas_m3', 0) or 0)
        lpg_kg = float(request.form.get('lpg_kg', 0) or 0)
        coal_kg = float(request.form.get('coal_kg', 0) or 0)
        fuel_oil_litres = float(request.form.get('fuel_oil_litres', 0) or 0)
        eu_importer = request.form.get('eu_importer', '')
        import_country = request.form.get('import_country', '')
        import_quantity = float(request.form.get('import_quantity', quantity) or quantity)
        eu_carbon_price = float(request.form.get('eu_carbon_price', 85) or 85)

        has_actual_data = electricity_kwh > 0 or diesel_litres > 0 or natural_gas_m3 > 0
        if has_actual_data:
            calc_result = calculator.calculate_embedded_emissions(
                product_type=product_type,
                production_quantity=quantity,
                electricity_kwh=electricity_kwh,
                diesel_litres=diesel_litres,
                natural_gas_m3=natural_gas_m3,
                lpg_kg=lpg_kg,
                coal_kg=coal_kg,
                fuel_oil_litres=fuel_oil_litres
            )
            methodology = 'Actual data based on GHG Protocol'
            factor_source = 'Malaysian grid factor (0.7 kg/kWh) + IPCC AR6'
        else:
            calc_result = calculator.calculate_with_c_bam_defaults(
                product_type=product_type,
                production_quantity=quantity,
                has_actual_data=False
            )
            methodology = 'EU CBAM Default Values'
            factor_source = 'EU CBAM Default Values 2025'

        ee_per_tonne = calc_result['embedded_emissions_per_unit'] / 1000
        cert_need = calculator.calculate_cbam_certificate_need(
            embedded_emissions_per_tonne=ee_per_tonne,
            quantity_imported=import_quantity if import_quantity > 0 else quantity,
            eu_ets_carbon_price=eu_carbon_price
        )

        report_data = {
            'report_id': f"CBAM-MY-{uuid.uuid4().hex[:8].upper()}",
            'reporting_period': reporting_period,
            'company_name': company_name, 'facility_id': facility_id,
            'facility_address': facility_address, 'country': 'MY',
            'contact_person': contact_person, 'contact_email': contact_email,
            'product_type': product_type, 'product_description': product_description,
            'quantity': quantity, 'unit': unit, 'production_period': production_period,
            'scope1_kg': calc_result['scope1_emissions_kg'],
            'scope2_kg': calc_result['scope2_emissions_kg'],
            'embedded_emissions_kg': calc_result['total_emissions_kg'],
            'emissions_per_tonne': calc_result['embedded_emissions_per_unit'],
            'methodology': methodology, 'factor_source': factor_source,
            'eu_importer': eu_importer, 'import_country': import_country,
            'import_quantity': import_quantity if import_quantity > 0 else quantity,
            'verified': False,
            'uncertainty_percent': calc_result['uncertainty_percent'],
            'data_quality': 'Measured' if has_actual_data else 'Default',
            'certificate_need': cert_need
        }

        xml_filename = f"CBAM_{company_name.replace(' ', '_')}_{reporting_period}.xml"
        xml_path = generator.generate_cbam_xml(report_data, xml_filename)

        pdf_path = None
        try:
            pdf_filename = f"CBAM_{company_name.replace(' ', '_')}_{reporting_period}.pdf"
            pdf_path = generator.generate_pdf_report(report_data, pdf_filename)
        except ImportError:
            pass

        result_data = {
            'scope1_kg': f"{calc_result['scope1_emissions_kg']:,.2f}",
            'scope2_kg': f"{calc_result['scope2_emissions_kg']:,.2f}",
            'total_kg': f"{calc_result['total_emissions_kg']:,.2f}",
            'per_tonne': f"{calc_result['embedded_emissions_per_unit']:.2f}",
            'certificates_needed': int(cert_need['cbam_certificates_needed']),
            'carbon_price': eu_carbon_price,
            'cost_eur': f"{cert_need['total_cost_eur']:,.2f}",
            'xml_path': f"/download/{xml_filename}",
            'pdf_path': f"/download/{pdf_filename}" if pdf_path else None
        }

    factors = calculator.get_factor_summary()
    template = 'cbam_report.html' if lang == 'zh' else 'cbam_report_en.html'
    return render_template(template, result=result_data, factors=factors)

@app.route('/receipt', methods=['GET', 'POST'])
def receipt():
    """OCR receipt recognition (legacy)."""
    lang = get_lang()
    result = None
    image_path = None
    filename = None

    if request.method == 'POST':
        if 'image' not in request.files:
            return render_template('receipt.html', error='请选择图片')

        file = request.files['image']
        if file.filename == '':
            return render_template('receipt.html', error='请选择图片')

        ext = os.path.splitext(file.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        try:
            img = Image.open(filepath)
            w, h = img.size
            if w > h:
                img = img.rotate(90, expand=True)
            lang_tess = 'chi_sim+eng' if lang == 'zh' else 'eng'
            text = pytesseract.image_to_string(img, lang=lang_tess)
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            result = '\n'.join(lines)
            image_path = filepath
        except Exception as e:
            result = f"识别出错: {str(e)}"

    return render_template('receipt.html', result=result, image_path=image_path,
                           filename=filename, error=None)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# Legacy download for old reports
@app.route('/download/<filename>')
def legacy_download(filename):
    safe = os.path.basename(filename)
    fp = os.path.join(REPORTS_FOLDER, safe)
    if os.path.exists(fp):
        return send_from_directory(REPORTS_FOLDER, safe, as_attachment=True)
    return f"File not found: {safe}", 404

@app.route('/api/factors')
def get_factors():
    from cbam_calculator import CBAMCalculator
    return jsonify(CBAMCalculator().get_factor_summary())

# ============================================================
# SEED EMISSION FACTORS
# ============================================================
def seed_factors():
    if EmissionFactor.query.first():
        return
    factors = [
        ('electricity', 'peninsular', 0.571, 'kg CO₂/kWh', 'Suruhanjaya Tenaga Malaysia 2022', 2022, 'peninsular'),
        ('electricity', 'sabah', 0.758, 'kg CO₂/kWh', 'Suruhanjaya Tenaga Malaysia (SESB) 2022', 2022, 'sabah'),
        ('electricity', 'sarawak', 0.234, 'kg CO₂/kWh', 'Suruhanjaya Tenaga Malaysia (SESCO) 2022', 2022, 'sarawak'),
        ('fuel', 'diesel', 2.68, 'kg CO₂e/L', 'IPCC AR6', 2022, 'global'),
        ('fuel', 'petrol', 2.31, 'kg CO₂e/L', 'IPCC AR6', 2022, 'global'),
        ('fuel', 'natural_gas', 2.00, 'kg CO₂e/m³', 'IPCC AR6', 2022, 'global'),
        ('fuel', 'lpg', 2.98, 'kg CO₂e/kg', 'IPCC AR6', 2022, 'global'),
        ('fuel', 'coal', 2.42, 'kg CO₂e/kg', 'IPCC AR6', 2022, 'global'),
        ('fuel', 'coke', 3.17, 'kg CO₂e/kg', 'IPCC AR6', 2022, 'global'),
        ('fuel', 'fuel_oil', 3.17, 'kg CO₂e/kg', 'IPCC AR6', 2022, 'global'),
        ('cbam_default', 'steel_bof', 2.15, 'tonne CO₂e/tonne', 'EU CBAM 2025', 2025, 'global'),
        ('cbam_default', 'steel_eaf', 0.44, 'tonne CO₂e/tonne', 'EU CBAM 2025', 2025, 'global'),
        ('cbam_default', 'aluminium_primary', 8.38, 'tonne CO₂e/tonne', 'EU CBAM 2025', 2025, 'global'),
        ('cbam_default', 'cement_clinker', 0.83, 'tonne CO₂e/tonne', 'EU CBAM 2025', 2025, 'global'),
        ('cbam_default', 'ammonia', 1.98, 'tonne CO₂e/tonne', 'EU CBAM 2025', 2025, 'global'),
    ]
    for cat, sub, factor, unit, source, year, region in factors:
        ef = EmissionFactor(category=cat, subcategory=sub, factor=round(factor, 8),
                            unit=unit, source=source, year=year, region=region)
        db.session.add(ef)
    db.session.commit()

# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(exc):
    get_lang()
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(exc):
    get_lang()
    return render_template('500.html'), 500

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_factors()
        # Create admin account if not exists
        admin = User.query.filter_by(email='admin@carbonpass.com').first()
        if not admin:
            admin = User(
                email='admin@carbonpass.com', name='System Admin',
                role='system_admin', email_verified=True
            )
            admin.set_password('Admin1234')
            db.session.add(admin)
            db.session.commit()
            print("管理员账号已创建: admin@carbonpass.com / Admin1234")
    print("=" * 50)
    print("CarbonPass 碳排放核算平台已启动")
    print(f"数据库: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print("请在浏览器中打开: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(debug=True, port=5000)
