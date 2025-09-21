import google.generativeai as genai
import PyPDF2
from docx import Document
import pandas as pd
import os
import re
import json
from io import BytesIO

# Load environment variables
def load_api_key():
    try:
        with open('.env', 'r') as f:
            for line in f:
                if line.startswith('GEMINI_API_KEY='):
                    return line.split('=')[1].strip()
    except:
        return None

# Initialize Gemini
def initialize_gemini():
    api_key = load_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in .env file")
    
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-pro')

# Extract text from PDF
def extract_pdf_text(file):
    try:
        pdf_reader = PyPDF2.PdfReader(BytesIO(file.read()))
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text.strip()
    except Exception as e:
        return f"Error reading PDF: {str(e)}"

# Extract text from DOCX
def extract_docx_text(file):
    try:
        doc = Document(BytesIO(file.read()))
        text = ""
        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"
        return text.strip()
    except Exception as e:
        return f"Error reading DOCX: {str(e)}"

# Extract text from uploaded file
def extract_text_from_file(uploaded_file):
    if uploaded_file.type == "application/pdf":
        return extract_pdf_text(uploaded_file)
    elif uploaded_file.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx_text(uploaded_file)
    else:
        return "Unsupported file format"

# Clean and normalize text
def clean_text(text):
    # Remove extra whitespace and normalize
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n+', '\n', text)
    return text.strip()

# Parse job description using Gemini
def parse_job_description(jd_text, model):
    prompt = f"""
    Analyze this job description and extract key information in JSON format:
    
    Job Description: {jd_text}
    
    Extract:
    1. Job title
    2. Must-have skills (list of 5-10 specific technical skills)
    3. Good-to-have skills (list of 3-7 additional skills)
    4. Experience required
    5. Education requirements
    
    Return only valid JSON format like:
    {{
        "job_title": "Software Developer",
        "must_have_skills": ["Python", "JavaScript", "React", "SQL", "Git"],
        "good_to_have_skills": ["AWS", "Docker", "MongoDB"],
        "experience_required": "2-5 years",
        "education_required": "Bachelor's degree in Computer Science"
    }}
    """
    
    try:
        response = model.generate_content(prompt)
        json_str = response.text.strip()
        if json_str.startswith('```json'):
            json_str = json_str.replace('```json', '').replace('```', '').strip()
        
        parsed_data = json.loads(json_str)
        
        # Ensure we have lists
        if not isinstance(parsed_data.get('must_have_skills', []), list):
            parsed_data['must_have_skills'] = ["Python", "Communication", "Problem Solving"]
        if not isinstance(parsed_data.get('good_to_have_skills', []), list):
            parsed_data['good_to_have_skills'] = ["AWS", "Docker"]
            
        return parsed_data
        
    except Exception as e:
        print(f"Error parsing job description: {e}")
        # Fallback parsing
        return {
            "job_title": "Job Position",
            "must_have_skills": ["Python", "JavaScript", "SQL", "Communication", "Problem Solving"],
            "good_to_have_skills": ["AWS", "Docker", "MongoDB"],
            "experience_required": "2-5 years",
            "education_required": "Bachelor's degree"
        }

# Improved skill matching function
def calculate_skill_match_score(resume_text, must_have_skills, good_to_have_skills):
    """Calculate skill match score with better logic"""
    resume_lower = resume_text.lower()
    
    # Find matched must-have skills
    matched_must_have = []
    for skill in must_have_skills:
        if skill.lower() in resume_lower:
            matched_must_have.append(skill)
    
    # Find matched good-to-have skills
    matched_good_to_have = []
    for skill in good_to_have_skills:
        if skill.lower() in resume_lower:
            matched_good_to_have.append(skill)
    
    # Calculate scores
    must_have_score = (len(matched_must_have) / len(must_have_skills)) * 60 if must_have_skills else 0
    good_to_have_score = (len(matched_good_to_have) / len(good_to_have_skills)) * 15 if good_to_have_skills else 0
    base_score = 25  # Base qualification score
    
    total_score = int(base_score + must_have_score + good_to_have_score)
    
    # Find missing skills
    missing_must_have = [skill for skill in must_have_skills if skill not in matched_must_have]
    
    return {
        'total_score': min(100, total_score),
        'matched_skills': matched_must_have + matched_good_to_have,
        'missing_skills': missing_must_have,
        'skill_match_rate': (len(matched_must_have) / len(must_have_skills)) * 100 if must_have_skills else 0
    }

# Enhanced resume analysis
def analyze_resume(resume_text, job_data, model):
    # First, get basic skill matching
    skill_analysis = calculate_skill_match_score(
        resume_text, 
        job_data.get('must_have_skills', []), 
        job_data.get('good_to_have_skills', [])
    )
    
    # Enhanced prompt for better analysis
    prompt = f"""
    You are an expert HR professional evaluating a resume against job requirements.
    
    RESUME CONTENT (first 3000 chars):
    {resume_text[:3000]}
    
    JOB REQUIREMENTS:
    - Position: {job_data.get('job_title', 'Not specified')}
    - Must-have skills: {job_data.get('must_have_skills', [])}
    - Nice-to-have skills: {job_data.get('good_to_have_skills', [])}
    - Experience: {job_data.get('experience_required', 'Not specified')}
    - Education: {job_data.get('education_required', 'Not specified')}
    
    ANALYSIS FRAMEWORK:
    1. Technical Skills Match (0-40 points): How well do the candidate's technical skills align?
    2. Experience Relevance (0-25 points): Does their experience match the requirements?
    3. Educational Background (0-15 points): Does their education fit?
    4. Overall Profile Quality (0-20 points): Resume quality, achievements, certifications, etc.
    
    Analyze this candidate thoroughly and provide detailed feedback.
    
    Return ONLY valid JSON:
    {{
        "technical_skills_score": number_0_to_40,
        "experience_score": number_0_to_25,
        "education_score": number_0_to_15,
        "profile_quality_score": number_0_to_20,
        "experience_match": "Excellent Match" or "Good Match" or "Partial Match" or "Poor Match",
        "education_match": "Excellent Match" or "Good Match" or "Partial Match" or "Poor Match",
        "strengths": ["strength1", "strength2", "strength3"],
        "recommendations": ["improvement1", "improvement2", "improvement3"],
        "key_achievements": ["achievement1", "achievement2"],
        "years_of_experience": estimated_years_number
    }}
    """
    
    try:
        response = model.generate_content(prompt)
        json_str = response.text.strip()
        
        # Clean JSON response
        if json_str.startswith('```json'):
            json_str = json_str.replace('```json', '').replace('```', '').strip()
        if json_str.startswith('```'):
            json_str = json_str.replace('```', '').strip()
        
        # Parse the response
        ai_analysis = json.loads(json_str)
        
        # Calculate final score
        final_score = (
            ai_analysis.get('technical_skills_score', 20) +
            ai_analysis.get('experience_score', 15) +
            ai_analysis.get('education_score', 10) +
            ai_analysis.get('profile_quality_score', 10)
        )
        
        # Blend with skill matching score (weighted average)
        blended_score = int((final_score * 0.7) + (skill_analysis['total_score'] * 0.3))
        final_score = max(min(100, blended_score), 0)
        
        # Determine verdict
        if final_score >= 80:
            verdict = "Excellent Fit - Highly Recommended"
        elif final_score >= 65:
            verdict = "Good Fit - Recommended for Interview"
        elif final_score >= 50:
            verdict = "Moderate Fit - Consider with Reservations"
        else:
            verdict = "Poor Fit - Not Recommended"
        
        return {
            "overall_score": final_score,
            "verdict": verdict,
            "matched_skills": skill_analysis['matched_skills'],
            "missing_skills": skill_analysis['missing_skills'],
            "strengths": ai_analysis.get('strengths', ["Shows relevant background"]),
            "recommendations": ai_analysis.get('recommendations', ["Continue skill development"]),
            "experience_match": ai_analysis.get('experience_match', 'Average Match'),
            "education_match": ai_analysis.get('education_match', 'Satisfactory'),
            "key_achievements": ai_analysis.get('key_achievements', []),
            "years_of_experience": ai_analysis.get('years_of_experience', 0),
            "technical_skills_score": ai_analysis.get('technical_skills_score', 20),
            "experience_score": ai_analysis.get('experience_score', 15),
            "education_score": ai_analysis.get('education_score', 10),
            "profile_quality_score": ai_analysis.get('profile_quality_score', 10)
        }
        
    except Exception as e:
        print(f"AI analysis failed: {e}")
        
        # Enhanced fallback analysis
        resume_lower = resume_text.lower()
        
        # Better experience estimation
        years_mentioned = re.findall(r'(\d+)[\s\-]*(?:years?|yrs?)', resume_lower)
        estimated_years = max([int(y) for y in years_mentioned]) if years_mentioned else 1
        
        # Better skill-based scoring
        base_score = skill_analysis['total_score']
        
        # Experience bonus
        if estimated_years >= 5:
            base_score += 10
        elif estimated_years >= 3:
            base_score += 5
        
        # Education bonus
        if any(edu in resume_lower for edu in ['bachelor', 'master', 'phd', 'degree']):
            base_score += 8
        
        # Technical depth bonus
        tech_keywords = ['project', 'developed', 'implemented', 'designed', 'built', 'created']
        tech_count = sum(1 for word in tech_keywords if word in resume_lower)
        base_score += min(tech_count * 2, 10)
        
        final_score = min(100, base_score)
        
        if final_score >= 75:
            verdict = "Good Candidate - Proceed with Interview"
        elif final_score >= 55:
            verdict = "Potential Candidate - Review Carefully"
        else:
            verdict = "Below Requirements - Consider for Future"
        
        return {
            "overall_score": final_score,
            "verdict": verdict,
            "matched_skills": skill_analysis['matched_skills'],
            "missing_skills": skill_analysis['missing_skills'],
            "strengths": ["Technical background present", "Relevant experience indicated"],
            "recommendations": ["Strengthen missing technical skills", "Highlight specific achievements"],
            "experience_match": "Good Match" if estimated_years >= 2 else "Limited Experience",
            "education_match": "Adequate" if any(edu in resume_lower for edu in ['bachelor', 'master', 'degree']) else "Basic",
            "key_achievements": [],
            "years_of_experience": estimated_years
        }

# Process multiple resumes
def process_resumes(job_text, resume_files, model):
    print("Starting resume processing...")
    
    # Parse job description
    job_data = parse_job_description(job_text, model)
    print(f"Job parsed - Must have skills: {job_data.get('must_have_skills', [])}")
    
    results = []
    for i, uploaded_file in enumerate(resume_files):
        print(f"Processing resume {i+1}/{len(resume_files)}: {uploaded_file.name}")
        
        # Extract resume text
        resume_text = extract_text_from_file(uploaded_file)
        resume_text = clean_text(resume_text)
        
        if resume_text and not resume_text.startswith("Error") and len(resume_text.strip()) > 50:
            print(f"Extracted {len(resume_text)} characters from {uploaded_file.name}")
            
            # Analyze resume
            analysis = analyze_resume(resume_text, job_data, model)
            print(f"Score for {uploaded_file.name}: {analysis['overall_score']}")
            
            result = {
                "candidate_name": uploaded_file.name.replace('.pdf', '').replace('.docx', ''),
                "file_name": uploaded_file.name,
                **analysis
            }
            results.append(result)
        else:
            print(f"Failed to extract meaningful text from {uploaded_file.name}")
            # Handle error case
            results.append({
                "candidate_name": uploaded_file.name,
                "file_name": uploaded_file.name,
                "overall_score": 0,
                "verdict": "File Processing Error",
                "matched_skills": [],
                "missing_skills": job_data.get('must_have_skills', []),
                "strengths": [],
                "recommendations": ["File could not be processed - check file format"],
                "experience_match": "Unknown",
                "education_match": "Unknown"
            })
    
    # Sort by score (highest first)
    results.sort(key=lambda x: x['overall_score'], reverse=True)
    print(f"Processing complete. Scores: {[r['overall_score'] for r in results]}")
    
    return results, job_data