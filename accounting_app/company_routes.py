"""
Company gateway and settings routes
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from flask_login import login_required, current_user
import os
import shutil
from werkzeug.security import generate_password_hash

from database import (
    company_exists,
    create_company_profile,
    get_company_settings,
    update_company_profile,
    initialize_db,
    master_get_all_companies,
    master_get_user_companies,
    master_register_company,
    master_assign_company_to_user,
    master_get_company_by_id
)

from database.accounts_db import ensure_default_groups

from .models import admin_required, setup_access_required

company_bp = Blueprint('company_bp', __name__)

@company_bp.route('/company-gateway')
@login_required
def company_gateway():
    """Gateway page - select or create company"""
    # Get companies based on user role
    if current_user.is_admin:
        companies_data = master_get_all_companies()
    else:
        companies_data = master_get_user_companies(current_user.id)
    
    # Format for template
    companies = []
    for c in companies_data:
        companies.append({
            'id': c['id'],
            'name': c['name'],
            'path': '' 
        })
        
    return render_template('company_gateway.html', recent_companies=companies, is_admin=current_user.is_admin)

@company_bp.route('/select-company', methods=['POST'])
@login_required
def select_company():
    """Select existing company database"""
    company_id = request.form.get('company_id')
    
    if not company_id:
        flash('Invalid company selected', 'error')
        return redirect(url_for('company_bp.company_gateway'))
    
    try:
        company_id = int(company_id)
    except ValueError:
        flash('Invalid company ID', 'error')
        return redirect(url_for('company_bp.company_gateway'))

    # Verify permission
    allowed = False
    if current_user.is_admin:
        allowed = True
    else:
        user_companies = master_get_user_companies(current_user.id)
        for uc in user_companies:
            if uc['id'] == company_id:
                allowed = True
                break
    
    if not allowed:
        flash('You do not have permission to access this company.', 'error')
        return redirect(url_for('company_bp.company_gateway'))

    try:
        # Set session for company context
        session['company_id'] = company_id
        session.pop('active_location', None)  # Reset location switcher for new company

        # Get company settings
        # Pass company_id explicitly to ensure we get settings for the selected company
        company = get_company_settings(company_id=company_id)
        
        # Ensure default master groups and group links exist for this company
        ensure_default_groups(company_id)
        
        if company:
            session['company_name'] = company['company_name']
            flash(f'Loaded company: {company["company_name"]}', 'success')
        else:
            # Fallback if settings not found (new company?)
            # Retrieve name from companies table in Unified DB
            res = master_get_company_by_id(company_id)
            company_name = res['name'] if res else f"Company {company_id}"
            session['company_name'] = company_name
            flash(f'Loaded company: {company_name}', 'success')
            
            # Maybe redirect to company settings to complete setup?
            if not company:
                 # Check if we should initialize defaults here or if they are already done
                 pass

        return redirect(url_for('dashboard_bp.dashboard'))

    except Exception as e:
        flash(f'Error loading company: {str(e)}', 'error')
        return redirect(url_for('company_bp.company_gateway'))

@company_bp.route('/create-new-company', methods=['GET', 'POST'])
@login_required
@admin_required
def create_new_company():
    """Create new company in Unified DB"""
    if request.method == 'POST':
        company_name = request.form.get('company_name')
        
        if not company_name:
            flash('Company name is required', 'error')
            return render_template('create_company.html')
        
        try:
            # 1. Register in Master/Unified DB (creates entry in 'companies' table)
            # master_register_company now uses create_company logic under the hood
            # It returns the new company_id
            company_id = master_register_company(company_name)
            
            # 2. Assign to current user (admin) automatically
            master_assign_company_to_user(current_user.id, company_id, role='Admin')
            
            # 3. Create company profile (settings)
            create_company_profile(
                company_name=company_name,
                vat_registration_number=request.form.get('vat_registration_number', ''),
                address_line1=request.form.get('address_line1', ''),
                address_line2=request.form.get('address_line2', ''),
                city=request.form.get('city', ''),
                state=request.form.get('state', ''),
                country=request.form.get('country', ''),
                postal_code=request.form.get('postal_code', ''),
                phone=request.form.get('phone', ''),
                email=request.form.get('email', ''),
                inventory_applicable=1 if request.form.get('inventory_applicable') == 'on' else 0,
                vat_applicable=1 if request.form.get('vat_applicable') == 'on' else 0,
                multiple_locations_applicable=1 if request.form.get('multiple_locations_applicable') == 'on' else 0,
                cost_center_applicable=1 if request.form.get('cost_center_applicable') == 'on' else 0,
                cost_center_mandatory=1 if request.form.get('cost_center_mandatory') == 'on' else 0,

                currency_code=request.form.get('currency_code', 'AED'),
                company_id=company_id # Pass explicit company_id
            )
            
            # 4. Add locations if multiple locations is enabled
            if request.form.get('multiple_locations_applicable') == 'on':
                from database import add_location
                
                location_codes = request.form.getlist('location_code[]')
                location_names = request.form.getlist('location_name[]')
                location_addresses = request.form.getlist('location_address[]')
                
                for i, code in enumerate(location_codes):
                    if code and i < len(location_names) and location_names[i]:
                        address = location_addresses[i] if i < len(location_addresses) else ''
                        is_default = 1 if i == 0 else 0  # First location is default
                        add_location(code, location_names[i], address, '', '', is_default, company_id=company_id)
            
            # 5. Set session
            session['company_id'] = company_id
            session['company_name'] = company_name
            session.pop('active_location', None)  # Reset location switcher for new company
            
            flash(f'Company "{company_name}" created successfully!', 'success')
            return redirect(url_for('dashboard_bp.dashboard'))
        
        except Exception as e:
            flash(f'Error creating company: {str(e)}', 'error')
            return render_template('create_company.html')
    
    return render_template('create_company.html')

@company_bp.route('/company-settings', methods=['GET', 'POST'])
@login_required
@setup_access_required
def company_settings():
    """Company settings page for admins"""
    if request.method == 'POST':
        # Update using **kwargs
        update_company_profile(
            company_name=request.form['company_name'],
            vat_registration_number=request.form.get('vat_registration_number', ''),
            address_line1=request.form.get('address_line1', ''),
            address_line2=request.form.get('address_line2', ''),
            city=request.form.get('city', ''),
            state=request.form.get('state', ''),
            country=request.form.get('country', ''),
            postal_code=request.form.get('postal_code', ''),
            phone=request.form.get('phone', ''),
            email=request.form.get('email', ''),
            inventory_applicable=1 if request.form.get('inventory_applicable') == 'on' else 0,
            vat_applicable=1 if request.form.get('vat_applicable') == 'on' else 0,
            multiple_locations_applicable=1 if request.form.get('multiple_locations_applicable') == 'on' else 0,
            cost_center_applicable=1 if request.form.get('cost_center_applicable') == 'on' else 0,
            cost_center_mandatory=1 if request.form.get('cost_center_mandatory') == 'on' else 0,

            currency_code=request.form.get('currency_code', 'AED')
        )
        
        session['company_name'] = request.form['company_name']
        flash('Company settings updated successfully!', 'success')
        return redirect(url_for('company_bp.company_settings'))
    
    company = get_company_settings()
    return render_template('company_settings.html', company=company)

# ===== LOCATION/GODOWN MANAGEMENT =====

@company_bp.route('/set_active_location', methods=['POST'])
@login_required
def set_active_location():
    """Set the session-wide active location (main-menu Location Switcher)."""
    from database import get_locations
    location_name = (request.form.get('location_name') or '').strip()

    company = get_company_settings()
    if not company or not company.get('multiple_locations_applicable'):
        flash('Location-wise accounting is not enabled.', 'error')
        return redirect(request.referrer or url_for('dashboard_bp.dashboard'))

    valid_names = {l['location_name'] for l in get_locations()}
    if location_name not in valid_names:
        flash(f"Unknown location: {location_name}", 'error')
        return redirect(request.referrer or url_for('dashboard_bp.dashboard'))

    # Non-admin users may only switch to locations allocated to them
    if not current_user.is_admin:
        from database import get_user_locations
        allowed = get_user_locations(current_user.id)
        if allowed and location_name not in allowed:
            flash(f"You are not allowed to use location: {location_name}", 'error')
            return redirect(request.referrer or url_for('dashboard_bp.dashboard'))

    session['active_location'] = location_name
    flash(f'Active location switched to: {location_name}', 'success')
    return redirect(request.referrer or url_for('dashboard_bp.dashboard'))

@company_bp.route('/manage-locations', methods=['GET'])
@login_required
@setup_access_required
def manage_locations():
    """Manage locations/godowns"""
    from database import get_all_locations, get_company_settings
    
    # Check if multiple locations is enabled
    company = get_company_settings()
    if not company or not company.get('multiple_locations_applicable'):
        flash('Multiple locations feature is not enabled. Enable it in Company Settings first.', 'error')
        return redirect(url_for('company_bp.company_settings'))
    
    locations = get_all_locations()
    return render_template('manage_locations.html', 
                         locations=locations, 
                         username=current_user.username)

@company_bp.route('/add_location', methods=['POST'])
@login_required
@setup_access_required
def add_location_route():
    """Add new location"""
    from database import add_location
    
    location_code = request.form.get('location_code', '').strip()
    location_name = request.form.get('location_name', '').strip()
    address = request.form.get('address', '').strip()
    contact_person = request.form.get('contact_person', '').strip()
    phone = request.form.get('phone', '').strip()
    is_default = 1 if request.form.get('is_default') == 'on' else 0
    
    if not location_code or not location_name:
        return jsonify({'success': False, 'message': 'Location code and name are required'})
    
    success, message = add_location(location_code, location_name, address, contact_person, phone, is_default)
    return jsonify({'success': success, 'message': message})

@company_bp.route('/update_location', methods=['POST'])
@login_required
@setup_access_required
def update_location_route():
    """Update existing location"""
    from database import update_location
    
    location_id = request.form.get('location_id')
    location_code = request.form.get('location_code', '').strip()
    location_name = request.form.get('location_name', '').strip()
    address = request.form.get('address', '').strip()
    contact_person = request.form.get('contact_person', '').strip()
    phone = request.form.get('phone', '').strip()
    is_default = 1 if request.form.get('is_default') == 'on' else 0
    
    if not location_id or not location_code or not location_name:
        return jsonify({'success': False, 'message': 'Location ID, code and name are required'})
    
    success, message = update_location(int(location_id), location_code, location_name, address, contact_person, phone, is_default)
    return jsonify({'success': success, 'message': message})

@company_bp.route('/delete_location', methods=['POST'])
@login_required
@setup_access_required
def delete_location_route():
    """Delete location"""
    from database import delete_location
    
    location_id = request.form.get('location_id')
    
    if not location_id:
        return jsonify({'success': False, 'message': 'Location ID is required'})
    
    success, message = delete_location(int(location_id))
    return jsonify({'success': success, 'message': message})

@company_bp.route('/activate_location', methods=['POST'])
@login_required
@setup_access_required
def activate_location_route():
    """Activate previously inactive location"""
    from database import activate_location

    location_id = request.form.get('location_id')

    if not location_id:
        return jsonify({'success': False, 'message': 'Location ID is required'})

    success, message = activate_location(int(location_id))
    return jsonify({'success': success, 'message': message})
