from flask import Flask, render_template, request, jsonify, session, send_file
from werkzeug.utils import secure_filename
import pandas as pd
from processor import initialize_gemini, process_resumes, extract_text_from_file, clean_text
import json
import base64
from io import BytesIO
import matplotlib.pyplot as plt
import numpy as np
import os
from datetime import datetime
import uuid
import tempfile
import shutil

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Change this to a secure secret key
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class StreamlitFileWrapper:
    """Wrapper to mimic Streamlit's UploadedFile interface"""
    def __init__(self, file_path, original_name):
        self.name = original_name
        self._file_path = file_path
        self._file_handle = None
        
        # Set the type attribute based on file extension
        if original_name.lower().endswith('.pdf'):
            self.type = 'application/pdf'
        elif original_name.lower().endswith('.docx'):
            self.type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        elif original_name.lower().endswith('.doc'):
            self.type = 'application/msword'
        else:
            self.type = 'application/octet-stream'
    
    def read(self, size=-1):
        if self._file_handle is None:
            self._file_handle = open(self._file_path, 'rb')
        if size == -1:
            return self._file_handle.read()
        else:
            return self._file_handle.read(size)
    
    def seek(self, offset, whence=0):
        if self._file_handle is None:
            self._file_handle = open(self._file_path, 'rb')
        return self._file_handle.seek(offset, whence)
    
    def tell(self):
        if self._file_handle is None:
            self._file_handle = open(self._file_path, 'rb')
        return self._file_handle.tell()
    
    def close(self):
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

def init_session():
    """Initialize session variables"""
    if 'results' not in session:
        session['results'] = []
    if 'job_data' not in session:
        session['job_data'] = {}
    if 'current_candidate' not in session:
        session['current_candidate'] = 0
    if 'active_section' not in session:
        session['active_section'] = "overview"

def create_gauge_chart_data(score, title):
    """Create gauge chart data for frontend"""
    if score >= 75:
        color_class = "gauge-excellent"
        status = "Excellent"
        emoji = "🏆"
    elif score >= 50:
        color_class = "gauge-good"
        status = "Good"
        emoji = "⭐"
    else:
        color_class = "gauge-fair"
        status = "Fair"
        emoji = "🎯"
    
    return {
        'score': score,
        'title': title,
        'status': status,
        'emoji': emoji,
        'color_class': color_class
    }

def create_progress_bar_data(label, value, max_value=100):
    """Create progress bar data"""
    percentage = (value / max_value) * 100
    return {
        'label': label,
        'value': value,
        'max_value': max_value,
        'percentage': percentage
    }

def calculate_metrics(result):
    """Calculate various metrics for the candidate - IMPROVED VERSION"""
    matched_count = len(result.get("matched_skills", []))
    missing_count = len(result.get("missing_skills", []))
    total_skills = matched_count + missing_count
    
    # Use actual scores from AI analysis if available, otherwise fall back to computed scores
    base_score = result["overall_score"]
    
    metrics = {
        'skills_score': result.get('technical_skills_score', min(100, base_score * 0.4)),
        'exp_score': result.get('experience_score', min(100, base_score * 0.25)) if result["experience_match"] != "Unknown" else base_score * 0.25,
        'edu_score': result.get('education_score', min(100, base_score * 0.15)) if result["education_match"] != "Unknown" else base_score * 0.15,
        'keyword_score': min(100, base_score * 0.2),  # Profile quality score
        'matched_count': matched_count,
        'missing_count': missing_count,
        'total_skills': total_skills,
        'match_rate': (matched_count / total_skills * 100) if total_skills > 0 else 0,
        'confidence': min(95, base_score + 5)  # More realistic confidence calculation
    }
    
    return metrics

@app.route('/')
def index():
    """Main page route"""
    init_session()
    
    # Check if we have results to show
    if session.get('results'):
        return render_template('index.html', 
                             has_results=True,
                             results=session['results'],
                             current_candidate=session['current_candidate'],
                             active_section=session['active_section'])
    else:
        return render_template('index.html', has_results=False)

@app.route('/check_job_status')
def check_job_status():
    """Check if job description is already uploaded"""
    job_text = session.get('job_text', '')
    return jsonify({
        'success': True,
        'has_job_description': bool(job_text.strip()),
        'job_length': len(job_text) if job_text else 0
    })

@app.route('/upload_job_description', methods=['POST'])
def upload_job_description():
    """Handle job description upload - FIXED VERSION"""
    try:
        job_text = request.form.get('job_text', '')
        job_file = request.files.get('job_file')
        extracted_text = ""
        
        if job_file and job_file.filename:
            print(f"Processing job file: {job_file.filename}")
            # Save the file temporarily
            filename = secure_filename(job_file.filename)
            temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_jd_{uuid.uuid4()}_{filename}")
            job_file.save(temp_filepath)
            
            try:
                # Create a wrapper that matches the interface your processor expects
                file_wrapper = StreamlitFileWrapper(temp_filepath, job_file.filename)
                
                # Extract text using your existing processor function
                extracted_text = extract_text_from_file(file_wrapper)
                if extracted_text and not extracted_text.startswith("Error"):
                    job_text = clean_text(extracted_text)
                    print(f"Extracted {len(job_text)} characters from job file")
                
                # Close the file wrapper
                file_wrapper.close()
                
                if not job_text.strip():
                    return jsonify({'success': False, 'error': 'No text could be extracted from the file'})
                
            except Exception as e:
                print(f"Error extracting text from job file: {e}")
                return jsonify({'success': False, 'error': f'Error extracting text from file: {str(e)}'})
            finally:
                # Clean up the temporary file
                try:
                    os.remove(temp_filepath)
                except:
                    pass
        
        # Store the job text in session
        if job_text.strip():
            session['job_text'] = job_text.strip()
            print(f"Job description stored in session: {len(job_text)} characters")
            return jsonify({
                'success': True, 
                'message': f'Job description processed successfully ({len(job_text)} characters)',
                'text_length': len(job_text),
                'extracted_text': job_text  # Return extracted text for frontend
            })
        else:
            return jsonify({'success': False, 'error': 'No job description text provided'})
    
    except Exception as e:
        print(f"Error in upload_job_description: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/upload_resumes', methods=['POST'])
def upload_resumes():
    """Handle resume files upload"""
    try:
        files = request.files.getlist('resume_files')
        
        if not files or not any(f.filename for f in files):
            return jsonify({'success': False, 'error': 'No files uploaded'})
        
        valid_files = [f for f in files if f.filename]
        session['resume_count'] = len(valid_files)
        
        # Store files temporarily for processing
        temp_files = []
        for file in valid_files:
            filename = secure_filename(file.filename)
            temp_filename = f"{uuid.uuid4()}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
            file.save(filepath)
            temp_files.append(filepath)
            print(f"Saved resume file: {filepath}")
        
        session['temp_resume_files'] = temp_files
        
        return jsonify({'success': True, 'message': f'{len(temp_files)} files uploaded successfully'})
    
    except Exception as e:
        print(f"Error uploading resumes: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/analyze', methods=['POST'])
def analyze():
    """Process the analysis - ENHANCED VERSION"""
    try:
        job_text = session.get('job_text')
        temp_files = session.get('temp_resume_files', [])
        
        print(f"Starting analysis with job_text length: {len(job_text) if job_text else 0}")
        print(f"Number of resume files: {len(temp_files)}")
        
        if not job_text:
            return jsonify({'success': False, 'error': 'No job description provided'})
        
        if not temp_files:
            return jsonify({'success': False, 'error': 'No resume files provided'})
        
        # Initialize Gemini
        print("Initializing Gemini model...")
        model = initialize_gemini()
        
        # Create file objects for processing
        resume_files = []
        for filepath in temp_files:
            if os.path.exists(filepath):
                resume_files.append(StreamlitFileWrapper(filepath, os.path.basename(filepath)))
            else:
                print(f"Warning: File not found: {filepath}")
        
        if not resume_files:
            return jsonify({'success': False, 'error': 'No valid resume files found'})
        
        print(f"Processing {len(resume_files)} resume files...")
        
        # Process resumes with enhanced processor
        results, job_data = process_resumes(job_text, resume_files, model)
        
        # Close all file wrappers
        for file_wrapper in resume_files:
            file_wrapper.close()
        
        print(f"Analysis complete. Results: {len(results)} candidates processed")
        for result in results:
            print(f"- {result['candidate_name']}: {result['overall_score']} points")
        
        # Store results in session
        session['results'] = results
        session['job_data'] = job_data
        session['current_candidate'] = 0
        session['active_section'] = 'overview'
        
        # Clean up temporary files
        for filepath in temp_files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    print(f"Cleaned up: {filepath}")
            except Exception as e:
                print(f"Error cleaning up {filepath}: {e}")
        
        session.pop('temp_resume_files', None)
        
        return jsonify({
            'success': True, 
            'message': f'Analysis complete! Processed {len(results)} resumes',
            'results_count': len(results)
        })
    
    except Exception as e:
        print(f"Error during analysis: {e}")
        # Clean up temporary files on error
        temp_files = session.get('temp_resume_files', [])
        for filepath in temp_files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
        session.pop('temp_resume_files', None)
        
        return jsonify({'success': False, 'error': str(e)})

@app.route('/get_results')
def get_results():
    """Get current results data"""
    try:
        results = session.get('results', [])
        current_candidate = session.get('current_candidate', 0)
        
        if not results or current_candidate >= len(results):
            return jsonify({'success': False, 'error': 'No results available'})
        
        current_result = results[current_candidate]
        metrics = calculate_metrics(current_result)
        
        # Prepare candidate names for selector
        candidate_names = [
            f"{r['candidate_name']} ({r['overall_score']}/100)" 
            for r in results
        ]
        
        # Create gauge data
        overall_gauge = create_gauge_chart_data(current_result["overall_score"], "Overall Score")
        confidence_gauge = create_gauge_chart_data(metrics['confidence'], "AI Confidence")
        
        # Determine verdict styling - IMPROVED
        verdict = current_result["verdict"]
        score = current_result["overall_score"]
        if score >= 75 or "excellent" in verdict.lower() or "highly recommended" in verdict.lower():
            verdict_class = "verdict-excellent"
        elif score >= 50 or "good" in verdict.lower() or "recommended" in verdict.lower():
            verdict_class = "verdict-good"
        else:
            verdict_class = "verdict-fair"
        
        # Create progress bars data
        progress_bars = [
            create_progress_bar_data("Technical Skills", int(metrics['skills_score'])),
            create_progress_bar_data("Experience", int(metrics['exp_score'])),
            create_progress_bar_data("Education", int(metrics['edu_score'])),
            create_progress_bar_data("Profile Quality", int(metrics['keyword_score']))
        ]
        
        response_data = {
            'success': True,
            'current_result': current_result,
            'metrics': metrics,
            'candidate_names': candidate_names,
            'current_candidate': current_candidate,
            'total_candidates': len(results),
            'overall_gauge': overall_gauge,
            'confidence_gauge': confidence_gauge,
            'verdict_class': verdict_class,
            'progress_bars': progress_bars,
            'active_section': session.get('active_section', 'overview')
        }
        
        return jsonify(response_data)
    
    except Exception as e:
        print(f"Error in get_results: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/set_candidate/<int:candidate_index>')
def set_candidate(candidate_index):
    """Set current candidate"""
    results = session.get('results', [])
    if 0 <= candidate_index < len(results):
        session['current_candidate'] = candidate_index
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid candidate index'})

@app.route('/set_section/<section_name>')
def set_section(section_name):
    """Set active section"""
    valid_sections = ['overview', 'skills', 'analysis', 'insights']
    if section_name in valid_sections:
        session['active_section'] = section_name
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid section'})

@app.route('/get_section_data/<section_name>')
def get_section_data(section_name):
    """Get data for specific section"""
    try:
        results = session.get('results', [])
        current_candidate = session.get('current_candidate', 0)
        
        if not results or current_candidate >= len(results):
            return jsonify({'success': False, 'error': 'No results available'})
        
        current_result = results[current_candidate]
        metrics = calculate_metrics(current_result)
        
        if section_name == 'skills':
            # Skills section data
            data = {
                'matched_skills': current_result.get('matched_skills', []),
                'missing_skills': current_result.get('missing_skills', []),
                'matched_count': metrics['matched_count'],
                'missing_count': metrics['missing_count'],
                'match_rate': metrics['match_rate']
            }
        
        elif section_name == 'analysis':
            # Analysis section data
            data = {
                'strengths': current_result.get('strengths', []),
                'recommendations': current_result.get('recommendations', []),
                'experience_match': current_result.get('experience_match', ''),
                'education_match': current_result.get('education_match', ''),
                'key_achievements': current_result.get('key_achievements', []),
                'years_of_experience': current_result.get('years_of_experience', 0)
            }
        
        elif section_name == 'insights':
            # Insights section data
            score = current_result["overall_score"]
            if score >= 80:
                assessment_color = "#10b981"
                assessment_icon = "🏆"
                recommendation = "Highly recommend for immediate interview"
                hire_probability = min(95, score + 5)
                training_time = "1-2 weeks"
                risk_level = "Low"
                risk_icon = "✅"
            elif score >= 65:
                assessment_color = "#059669"
                assessment_icon = "⭐"
                recommendation = "Strong candidate - recommend for interview"
                hire_probability = min(90, score + 5)
                training_time = "2-4 weeks"
                risk_level = "Low"
                risk_icon = "✅"
            elif score >= 50:
                assessment_color = "#f59e0b"
                assessment_icon = "⚡"
                recommendation = "Consider for interview with skill development plan"
                hire_probability = min(75, score + 5)
                training_time = "1-3 months"
                risk_level = "Medium"
                risk_icon = "⚠️"
            else:
                assessment_color = "#ef4444"
                assessment_icon = "🎯"
                recommendation = "May require significant upskilling before interview"
                hire_probability = min(60, score + 10)
                training_time = "3-6 months"
                risk_level = "High"
                risk_icon = "❌"
            
            data = {
                'overall_score': score,
                'verdict': current_result["verdict"],
                'assessment_color': assessment_color,
                'assessment_icon': assessment_icon,
                'recommendation': recommendation,
                'hire_probability': hire_probability,
                'training_time': training_time,
                'risk_level': risk_level,
                'risk_icon': risk_icon,
                'skills_alignment': metrics['match_rate'],
                'experience_match': current_result.get('experience_match', ''),
                'education_match': current_result.get('education_match', ''),
                'years_of_experience': current_result.get('years_of_experience', 0)
            }
        
        else:  # overview
            data = {
                'progress_bars': [
                    create_progress_bar_data("Technical Skills", int(metrics['skills_score'])),
                    create_progress_bar_data("Experience", int(metrics['exp_score'])),
                    create_progress_bar_data("Education", int(metrics['edu_score'])),
                    create_progress_bar_data("Profile Quality", int(metrics['keyword_score']))
                ],
                'matched_count': metrics['matched_count'],
                'missing_count': metrics['missing_count'],
                'match_rate': metrics['match_rate']
            }
        
        return jsonify({'success': True, 'data': data})
    
    except Exception as e:
        print(f"Error in get_section_data: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download_report')
def download_report():
    """Generate and download report"""
    try:
        results = session.get('results', [])
        current_candidate = session.get('current_candidate', 0)
        
        if not results or current_candidate >= len(results):
            return jsonify({'success': False, 'error': 'No results available'})
        
        current_result = results[current_candidate]
        
        # Enhanced report content
        report_content = f"""AI RESUME MATCHER PRO - DETAILED ANALYSIS REPORT
=====================================================

Candidate: {current_result['candidate_name']}
Overall Score: {current_result['overall_score']}/100
AI Verdict: {current_result['verdict']}
Years of Experience: {current_result.get('years_of_experience', 'Not specified')}

DETAILED SCORING BREAKDOWN:
--------------------------
Technical Skills Score: {current_result.get('technical_skills_score', 'N/A')}/40
Experience Score: {current_result.get('experience_score', 'N/A')}/25
Education Score: {current_result.get('education_score', 'N/A')}/15
Profile Quality Score: {current_result.get('profile_quality_score', 'N/A')}/20

SKILLS ANALYSIS:
---------------
Matched Skills ({len(current_result.get('matched_skills', []))}):
{', '.join(current_result.get('matched_skills', [])) if current_result.get('matched_skills') else 'None identified'}

Missing Skills ({len(current_result.get('missing_skills', []))}):
{', '.join(current_result.get('missing_skills', [])) if current_result.get('missing_skills') else 'None'}

EXPERIENCE & EDUCATION:
----------------------
Experience Match: {current_result.get('experience_match', 'N/A')}
Education Match: {current_result.get('education_match', 'N/A')}

KEY ACHIEVEMENTS:
----------------
{chr(10).join([f"• {achievement}" for achievement in current_result.get('key_achievements', [])]) if current_result.get('key_achievements') else '• No specific achievements identified'}

STRENGTHS:
---------
{chr(10).join([f"• {strength}" for strength in current_result.get('strengths', [])]) if current_result.get('strengths') else '• Profile shows general potential'}

RECOMMENDATIONS FOR IMPROVEMENT:
-------------------------------
{chr(10).join([f"• {rec}" for rec in current_result.get('recommendations', [])]) if current_result.get('recommendations') else '• Continue with standard evaluation process'}

Generated by AI Resume Matcher Pro
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Analysis Engine: Gemini Pro with Enhanced Scoring Algorithm
"""
        
        # Create a temporary file
        temp_file = BytesIO()
        temp_file.write(report_content.encode('utf-8'))
        temp_file.seek(0)
        
        return send_file(
            temp_file,
            as_attachment=True,
            download_name=f"resume_report_{current_result['candidate_name']}.txt",
            mimetype='text/plain'
        )
    
    except Exception as e:
        print(f"Error generating report: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/export_csv')
def export_csv():
    """Export all results to CSV - ENHANCED VERSION"""
    try:
        results = session.get('results', [])
        
        if not results:
            return jsonify({'success': False, 'error': 'No results available'})
        
        export_data = []
        for result in results:
            export_data.append({
                'Candidate Name': result['candidate_name'],
                'Overall Score': result['overall_score'],
                'AI Verdict': result['verdict'],
                'Technical Skills Score': result.get('technical_skills_score', 'N/A'),
                'Experience Score': result.get('experience_score', 'N/A'),
                'Education Score': result.get('education_score', 'N/A'),
                'Profile Quality Score': result.get('profile_quality_score', 'N/A'),
                'Years of Experience': result.get('years_of_experience', 'N/A'),
                'Experience Match': result.get('experience_match', 'N/A'),
                'Education Match': result.get('education_match', 'N/A'),
                'Matched Skills Count': len(result.get('matched_skills', [])),
                'Missing Skills Count': len(result.get('missing_skills', [])),
                'Matched Skills': ', '.join(result.get('matched_skills', [])) if result.get('matched_skills') else '',
                'Missing Skills': ', '.join(result.get('missing_skills', [])) if result.get('missing_skills') else '',
                'Key Achievements': ' | '.join(result.get('key_achievements', [])) if result.get('key_achievements') else '',
                'Strengths': ' | '.join(result.get('strengths', [])) if result.get('strengths') else '',
                'Recommendations': ' | '.join(result.get('recommendations', [])) if result.get('recommendations') else ''
            })
        
        df = pd.DataFrame(export_data)
        
        # Create CSV in memory
        temp_file = BytesIO()
        df.to_csv(temp_file, index=False)
        temp_file.seek(0)
        
        return send_file(
            temp_file,
            as_attachment=True,
            download_name="resume_analysis_results.csv",
            mimetype='text/csv'
        )
    
    except Exception as e:
        print(f"Error exporting CSV: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/reset')
def reset():
    """Reset the application state"""
    # Clean up any temporary files before clearing session
    temp_files = session.get('temp_resume_files', [])
    for filepath in temp_files:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
    
    session.clear()
    return jsonify({'success': True, 'message': 'Application reset successfully'})

@app.route('/batch_analysis')
def batch_analysis():
    """Get batch analysis data for multiple candidates"""
    try:
        results = session.get('results', [])
        
        if len(results) <= 1:
            return jsonify({'success': False, 'error': 'Need multiple candidates for batch analysis'})
        
        scores = [result['overall_score'] for result in results]
        avg_score = np.mean(scores)
        max_score = max(scores)
        min_score = min(scores)
        excellent_count = len([s for s in scores if s >= 75])
        good_count = len([s for s in scores if 50 <= s < 75])
        poor_count = len([s for s in scores if s < 50])
        
        # Top candidates
        sorted_results = sorted(results, key=lambda x: x['overall_score'], reverse=True)
        top_candidates = []
        
        for i, result in enumerate(sorted_results[:5], 1):  # Show top 5 instead of top 3
            if i == 1:
                rank_color = "#FFD700"
                rank_icon = "🥇"
            elif i == 2:
                rank_color = "#C0C0C0"
                rank_icon = "🥈"
            elif i == 3:
                rank_color = "#CD7F32"
                rank_icon = "🥉"
            else:
                rank_color = "#6B7280"
                rank_icon = f"#{i}"
            
            top_candidates.append({
                'rank': i,
                'name': result['candidate_name'],
                'score': result['overall_score'],
                'verdict': result['verdict'],
                'rank_color': rank_color,
                'rank_icon': rank_icon
            })
        
        batch_data = {
            'avg_score': round(avg_score, 1),
            'max_score': max_score,
            'min_score': min_score,
            'excellent_count': excellent_count,
            'good_count': good_count,
            'poor_count': poor_count,
            'total_candidates': len(results),
            'top_candidates': top_candidates
        }
        
        return jsonify({'success': True, 'data': batch_data})
    
    except Exception as e:
        print(f"Error in batch_analysis: {e}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)